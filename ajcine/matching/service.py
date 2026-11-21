"""Title lookup through the TMDB API (no local database, nothing to download or import).

Search runs against TMDB's movie and TV endpoints; the best few results are then fetched in detail
(genres, runtime, alternative titles and, importantly, the external IMDb id). Catalog ids stay the IMDb id
("tt1375666") because TMDB reports it for almost every title; a result TMDB has no IMDb id for is not offered,
so a file for such a title goes to the review queue (where it can be saved as a local title) instead of being
guessed. Ratings and vote counts are TMDB's.

This product uses the TMDB API but is not endorsed or certified by TMDB.
Lookups are cached in MongoDB (`title_cache`, expires after 30 days; TMDB asks that cached data stay current).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .. import db as dbm
from ..config import Settings
from ..parsing.text import match_key
from ..tmdb import TmdbClient, TmdbError
from .models import Candidate, MatchQuery, rank_candidates

log = logging.getLogger("ajcine.titles")

_ID_RE = re.compile(r"^tt\d{7,10}$")


def valid_imdb_id(value: str | None) -> bool:
    return bool(value and _ID_RE.match(value))


class LookupUnavailable(RuntimeError):
    """TMDB cannot be queried (not configured, bad key, rate limit, network)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _year(date: str | None) -> int | None:
    d = (date or "")[:4]
    return int(d) if d.isdigit() and int(d) > 1800 else None


class TitleService:
    def __init__(self, settings: Settings, db: Any, cache: Any | None = None, client: TmdbClient | None = None):
        self.settings = settings
        self.db = db
        self.cache = cache
        self.tmdb = client or TmdbClient(settings)
        self._genres: dict[str, dict[int, str]] = {}

    # ---- lifecycle ---------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.tmdb.enabled

    async def reload(self) -> None:           # kept for the callers that refresh after configuration changes
        self._genres.clear()

    async def close(self) -> None:
        await self.tmdb.close()

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            return await self.tmdb._get(path, params, strict=True)
        except TmdbError as exc:
            raise LookupUnavailable(str(exc)) from exc

    # ---- cache ---------------------------------------------------------------------------------------
    async def _cached(self, key: str) -> Any | None:
        doc = await self.db[dbm.TITLE_CACHE].find_one({"key": key})
        return doc["value"] if doc else None

    async def _store(self, key: str, value: Any) -> None:
        await self.db[dbm.TITLE_CACHE].update_one({"key": key}, {"$set": {"key": key, "value": value, "created_at": _now()}},
                                                  upsert=True)

    # ---- mapping -------------------------------------------------------------------------------------
    async def _genre_names(self, kind: str) -> dict[int, str]:
        if kind not in self._genres:
            data = await self._get(f"/genre/{'tv' if kind == 'series' else 'movie'}/list") or {}
            self._genres[kind] = {g["id"]: g["name"] for g in data.get("genres", [])}
        return self._genres[kind]

    async def _from_search(self, r: dict, kind: str) -> Candidate | None:
        title = r.get("title") or r.get("name")
        if not title:
            return None
        names = await self._genre_names(kind)
        orig = r.get("original_title") or r.get("original_name")
        return Candidate(
            imdb_id=f"tmdb:{kind}:{r['id']}",                    # placeholder until the detail call supplies the IMDb id
            title=str(title), year=_year(r.get("release_date") or r.get("first_air_date")), kind=kind,
            original_title=orig if orig and orig != title else None,
            rating=round(float(r["vote_average"]), 1) if r.get("vote_average") else None,
            votes=int(r["vote_count"]) if r.get("vote_count") else None,
            genres=[names[g] for g in r.get("genre_ids", []) if g in names],
            plot=(r.get("overview") or "")[:700] or None)

    @staticmethod
    def _from_detail(d: dict, kind: str) -> Candidate | None:
        ext = d.get("external_ids") or {}
        imdb_id = d.get("imdb_id") or ext.get("imdb_id")
        title = d.get("title") or d.get("name")
        if not valid_imdb_id(imdb_id) or not title:
            return None
        alts = d.get("alternative_titles") or {}
        alt: list[str] = []
        for a in (alts.get("titles") or alts.get("results") or []):
            name = (a.get("title") or "").strip()
            if name and name != title and name not in alt:
                alt.append(name)
        orig = d.get("original_title") or d.get("original_name")
        runtime = d.get("runtime") or next(iter(d.get("episode_run_time") or []), None)
        return Candidate(
            imdb_id=imdb_id, title=str(title), year=_year(d.get("release_date") or d.get("first_air_date")), kind=kind,
            original_title=orig if orig and orig != title else None, alt_titles=alt[:30],
            rating=round(float(d["vote_average"]), 1) if d.get("vote_average") else None,
            votes=int(d["vote_count"]) if d.get("vote_count") else None,
            genres=[g["name"] for g in d.get("genres", []) if g.get("name")],
            runtime=int(runtime) if runtime else None, plot=(d.get("overview") or "")[:700] or None)

    async def _detail(self, kind: str, tmdb_id: int) -> Candidate | None:
        path = f"/{'tv' if kind == 'series' else 'movie'}/{tmdb_id}"
        d = await self._get(path, {"append_to_response": "external_ids,alternative_titles"})
        return self._from_detail(d, kind) if d else None

    # ---- search ----------------------------------------------------------------------------------------
    async def _search_raw(self, q: MatchQuery, limit: int, detail_top: int) -> list[dict]:
        kinds = [q.kind] if q.kind in ("movie", "series") else ["movie", "series"]
        pre: list[Candidate] = []
        for kind in kinds:
            params: dict = {"query": q.title, "include_adult": "false"}
            if q.year:
                params["year" if kind == "movie" else "first_air_date_year"] = q.year
            data = await self._get("/search/movie" if kind == "movie" else "/search/tv", params) or {}
            for r in (data.get("results") or [])[:limit]:
                c = await self._from_search(r, kind)
                if c:
                    pre.append(c)
        if q.year and not pre:                                     # the year in a file name can be wrong: retry without it
            return await self._search_raw(MatchQuery(q.title, None, q.kind, q.alt_titles), limit, detail_top)
        out: list[Candidate] = []
        for c in rank_candidates(q, pre)[:detail_top]:
            _, kind, tid = c.imdb_id.split(":")
            full = await self._detail(kind, int(tid))
            if full:
                out.append(full)
        return [c.to_dict() for c in out]

    async def search(self, q: MatchQuery, limit: int = 12, detail_top: int = 5) -> list[Candidate]:
        """Search TMDB and return candidates (with IMDb ids) ranked for the query."""
        if not q.title.strip():
            return []
        key = "s:" + hashlib.sha1(f"{match_key(q.title)}|{q.year}|{q.kind}".encode()).hexdigest()
        raw = await self._cached(key)
        if raw is None:
            raw = await self._search_raw(q, limit, detail_top)
            if raw:
                await self._store(key, raw)
        cands = [Candidate.from_dict(d) for d in raw]
        return rank_candidates(q, cands)

    # ---- details ------------------------------------------------------------------------------------------
    async def get(self, imdb_id: str) -> Candidate | None:
        if not valid_imdb_id(imdb_id):
            return None
        key = "t:" + imdb_id
        cached = await self._cached(key)
        if cached:
            return Candidate.from_dict(cached)
        found = await self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"}) or {}
        hit, kind = next(((r, "movie") for r in found.get("movie_results", [])), (None, None))
        if hit is None:
            hit, kind = next(((r, "series") for r in found.get("tv_results", [])), (None, None))
        if hit is None:
            return None
        cand = await self._detail(kind, hit["id"])
        if cand is None:
            return None
        cand.imdb_id = imdb_id                                      # the id the person asked for
        await self._store(key, cand.to_dict())
        return cand

    async def episode(self, imdb_id: str, season: int, episode: int) -> dict | None:
        """Best-effort episode title from TMDB; None when it has none (or on any lookup trouble)."""
        key = f"e:{imdb_id}:{season}:{episode}"
        try:
            cached = await self._cached(key)
            if cached is not None:
                return cached or None
            found = await self._get(f"/find/{imdb_id}", {"external_source": "imdb_id"}) or {}
            tv = next(iter(found.get("tv_results") or []), None)
            if not tv:
                return None
            data = await self._get(f"/tv/{tv['id']}/season/{int(season)}") or {}
            wanted = None
            for ep in data.get("episodes") or []:
                info = {"title": ep.get("name"), "year": _year(ep.get("air_date")), "plot": (ep.get("overview") or "")[:500] or None}
                await self._store(f"e:{imdb_id}:{season}:{ep.get('episode_number')}", info)
                if ep.get("episode_number") == episode:
                    wanted = info
            return wanted
        except LookupUnavailable as exc:
            log.debug("episode lookup failed: %s", exc)
            return None

    # ---- health ---------------------------------------------------------------------------------------------
    async def probe(self) -> dict:
        import time
        if not self.enabled:
            return {"ok": False, "error": "TMDB_API_KEY or TMDB_READ_TOKEN is not set"}
        t0 = time.time()
        try:
            data = await self._get("/search/movie", {"query": "The Matrix"}) or {}
        except LookupUnavailable as exc:
            return {"ok": False, "error": str(exc)[:300]}
        n = len(data.get("results") or [])
        return {"ok": n > 0, "results": n, "latency_ms": int((time.time() - t0) * 1000)}
