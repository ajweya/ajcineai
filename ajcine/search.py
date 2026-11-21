"""Search engine: exact, partial, fuzzy, typo/phonetic and AI-assisted search.

Retrieval is index-backed (words, phonetic words and character trigrams are
stored per title in `search_tokens`); ranking happens in-process with
rapidfuzz. Only titles that currently have at least one real indexed file are
returned. Results are cached in Redis keyed by a catalog version counter, so
any catalog change invalidates them.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db as dbm
from .ai.identify import AIIdentifier
from .catalog import Catalog
from .matching.models import similarity
from .parsing.languages import lang_code
from .parsing.parser import parse_filename
from .parsing.text import match_key, norm, tokens_for_index

log = logging.getLogger("ajcine.search")

PAGE_SIZE = 8
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean_query(text: str | None) -> str:
    t = _CTRL.sub(" ", text or "")
    return re.sub(r"\s+", " ", t).strip()[:100]


def _prefix_match(qtoks: list[str], names: list[str]) -> bool:
    for n in names:
        ntoks = n.split()
        used: set[int] = set()
        ok = True
        for qt in qtoks:
            hit = next((i for i, nt in enumerate(ntoks) if i not in used and nt.startswith(qt)), None)
            if hit is None:
                ok = False
                break
            used.add(hit)
        if ok:
            return True
    return False


def _pop(doc: dict) -> float:
    votes = math.log10((doc.get("votes") or 0) + 1) / 6.0
    files = math.log10((doc.get("file_count") or 0) + 1) / 2.0
    return min(1.0, 0.7 * min(votes, 1.0) + 0.3 * min(files, 1.0))


class SearchEngine:
    def __init__(self, db: Any, catalog: Catalog, cache: Any, ai: AIIdentifier | None = None):
        self.db = db
        self.catalog = catalog
        self.cache = cache
        self.ai = ai

    # ---- retrieval + ranking ---------------------------------------------------------------
    async def _retrieve(self, text: str, kind: str | None, language: str | None, deep: bool) -> list[dict]:
        toks = tokens_for_index(text)
        if not deep:
            toks = [t for t in toks if not t.startswith("#")]
        if not toks:
            return []
        flt: dict = {"search_tokens": {"$in": toks}, "file_count": {"$gt": 0}}
        if language:
            flt["languages"] = language
        colls = [dbm.MOVIES, dbm.SERIES]
        if kind == "movie":
            colls = [dbm.MOVIES]
        elif kind == "series":
            colls = [dbm.SERIES]
        out: list[dict] = []
        proj = {"search_tokens": 0}
        for c in colls:
            cur = self.db[c].find(flt, proj).sort([("popularity", -1)]).limit(600 if deep else 200)
            out.extend([d async for d in cur])
        return out

    def _rank(self, text: str, docs: list[dict], year: int | None, kind: str | None) -> list[dict]:
        qn = norm(text)
        qtoks = qn.split()
        ranked: list[dict] = []
        for d in docs:
            names = [d.get("title"), d.get("original_title"), *(d.get("alt_titles") or [])]
            names = [n for n in names if n]
            sim = max((similarity(text, n) for n in names), default=0.0)
            if sim < 0.88 and qtoks and _prefix_match(qtoks, [norm(n) for n in names]):
                sim = max(sim, 0.88)
            ysc = 0.5 if year is None else (1.0 if d.get("year") == year else 0.0 if d.get("year") else 0.3)
            tsc = 0.7 if kind is None else (1.0 if d.get("kind") == kind else 0.0)
            score = 0.65 * sim + 0.12 * ysc + 0.08 * tsc + 0.15 * _pop(d)
            if year is not None and d.get("year") != year and d.get("year") is not None:
                score *= 0.8
            ranked.append({"doc": d, "score": round(score, 4), "sim": round(sim, 4)})
        ranked.sort(key=lambda r: (r["score"], r["doc"].get("popularity") or 0), reverse=True)
        # duplicate suppression: same normalized title + year collapses to the best entry
        seen: set[tuple] = set()
        uniq = []
        for r in ranked:
            k = (match_key(r["doc"].get("title")), r["doc"].get("year"))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        return uniq

    @staticmethod
    def _item(r: dict) -> dict:
        d = r["doc"]
        return {
            "id": d["_id"], "title": d.get("title"), "year": d.get("year"), "kind": d.get("kind"),
            "rating": d.get("rating", d.get("imdb_rating")) if not str(d["_id"]).startswith("loc_") else None,
            "genres": d.get("genres") or [], "overview": d.get("overview"), "poster": d.get("poster"),
            "languages": d.get("languages") or [], "qualities": d.get("qualities") or [],
            "file_count": d.get("file_count") or 0, "verified": str(d["_id"]).startswith("tt"),
            "score": r["score"],
        }

    # ---- public API ----------------------------------------------------------------------------
    async def search(self, query: str, user_id: int | None = None, kind: str | None = None,
                     language: str | None = None, year: int | None = None, page: int = 1,
                     record: bool = True, use_ai: bool = True) -> dict:
        q = clean_query(query)
        if len(q) < 2:
            return {"query": q, "items": [], "total": 0, "page": 1, "pages": 0, "suggestions": [], "interpreted": None}

        parsed = parse_filename(q)
        text = parsed.title or q
        year = year or parsed.year
        kind = kind or (parsed.content_type if parsed.content_type == "series" else None)
        lang = language or (parsed.languages[0] if len(parsed.languages) == 1 else None)

        ver = await self.catalog.version()
        ckey = f"search:{ver}:{norm(text)}:{kind}:{lang}:{year}"
        cached = await self.cache.get(ckey)
        interpreted = None
        if cached is None:
            ranked = await self._run(text, kind, lang, year)
            if lang and not ranked:  # the language word was a soft hint
                ranked = await self._run(text, kind, None, year)
            if (not ranked or ranked[0]["score"] < 0.6) and use_ai and self.ai is not None and await self.ai.available():
                interp = await self.ai.interpret_query(q)
                if interp:
                    alt_ranked = await self._run(interp.title, interp.content_type or kind, None, interp.year or year)
                    for alt in interp.alt_titles[:2]:
                        alt_ranked += await self._run(alt, interp.content_type or kind, None, interp.year or year)
                    if alt_ranked and (not ranked or alt_ranked[0]["score"] > ranked[0]["score"]):
                        ranked = self._merge(alt_ranked)
                        interpreted = interp.title
            items = [self._item(r) for r in ranked]
            cached = {"items": items, "interpreted": interpreted}
            await self.cache.set(ckey, cached, ttl=120)
        items = cached["items"]
        interpreted = cached.get("interpreted")

        # Weak results are still shown, but flagged so the UI can say "did you mean".
        good = [i for i in items if i["score"] >= 0.45]
        total = len(good)
        pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 0
        page = max(1, min(page, pages or 1))
        window = good[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        suggestions = []
        if not good:
            suggestions = [{"id": i["id"], "title": i["title"], "year": i["year"]} for i in items[:5]]
        if record:
            await self._record(user_id, q, total)
        return {"query": q, "items": window, "total": total, "page": page, "pages": pages,
                "suggestions": suggestions, "interpreted": interpreted}

    async def _run(self, text: str, kind: str | None, lang: str | None, year: int | None) -> list[dict]:
        docs = await self._retrieve(text, kind, lang, deep=False)
        ranked = self._rank(text, docs, year, kind)
        if not ranked or ranked[0]["sim"] < 0.9:
            docs2 = await self._retrieve(text, kind, lang, deep=True)
            by_id = {d["_id"]: d for d in docs}
            for d in docs2:
                by_id.setdefault(d["_id"], d)
            ranked = self._rank(text, list(by_id.values()), year, kind)
        return ranked

    @staticmethod
    def _merge(ranked: list[dict]) -> list[dict]:
        best: dict[str, dict] = {}
        for r in ranked:
            cur = best.get(r["doc"]["_id"])
            if not cur or r["score"] > cur["score"]:
                best[r["doc"]["_id"]] = r
        return sorted(best.values(), key=lambda r: r["score"], reverse=True)

    async def _record(self, user_id: int | None, query: str, results: int) -> None:
        try:
            await self.db[dbm.SEARCH_HISTORY].insert_one({
                "user_id": user_id, "query": query, "norm_query": norm(query), "results": results,
                "ts": datetime.now(timezone.utc)})
        except Exception as exc:
            log.debug("search history write failed: %s", exc)

    # ---- discovery -----------------------------------------------------------------------------
    async def recent(self, limit: int = 8, kind: str | None = None) -> list[dict]:
        colls = [dbm.MOVIES, dbm.SERIES] if kind is None else [dbm.MOVIES if kind == "movie" else dbm.SERIES]
        docs: list[dict] = []
        for c in colls:
            cur = self.db[c].find({"file_count": {"$gt": 0}}, {"search_tokens": 0}).sort("last_indexed_at", -1).limit(limit)
            docs += [d async for d in cur]
        docs.sort(key=lambda d: d.get("last_indexed_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return [self._item({"doc": d, "score": 1.0}) for d in docs[:limit]]

    async def popular(self, limit: int = 8, days: int = 30) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        pipeline = [{"$match": {"ts": {"$gte": since}, "title_id": {"$ne": None}}},
                    {"$group": {"_id": "$title_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": limit * 2}]
        out: list[dict] = []
        async for row in self.db[dbm.DELIVERIES].aggregate(pipeline):
            doc = await self.catalog.get_title(row["_id"])
            if doc and (doc.get("file_count") or 0) > 0:
                out.append(self._item({"doc": doc, "score": 1.0}))
            if len(out) >= limit:
                break
        return out

    async def history(self, user_id: int, limit: int = 10) -> list[str]:
        cur = self.db[dbm.SEARCH_HISTORY].find({"user_id": user_id}).sort("ts", -1).limit(limit * 3)
        seen, out = set(), []
        async for d in cur:
            if d["norm_query"] not in seen:
                seen.add(d["norm_query"])
                out.append(d["query"])
            if len(out) >= limit:
                break
        return out
