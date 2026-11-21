"""Identification pipeline.

Order (cheapest and most trustworthy first):
  1. manual corrections   (always win)
  2. deterministic parse  (+ caption merge)
  3. TMDB match           (TMDB search + details, weighted scoring)
  4. AI normalisation     (OpenRouter free models) -> TMDB match again
  5. human review queue   (anything not confidently verified)

An AI guess is *never* approved by itself: it only supplies better search
text. The accepted identification is always a TMDB candidate (carrying its IMDb id) that passed the
scoring thresholds.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import db as dbm
from ..ai.identify import AIIdentifier, AIResult
from ..catalog import Catalog
from ..config import Settings
from ..matching.models import Candidate, MatchQuery, decide, rank_candidates
from ..matching.service import TitleService, LookupUnavailable
from ..parsing.parser import ParsedName, merge_parsed, parse_filename
from ..parsing.text import match_key

log = logging.getLogger("ajcine.identify")


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Identification:
    status: str                      # identified | pending_review | unidentified
    method: str                      # manual | tmdb | ai+tmdb | none
    parsed: ParsedName
    title_id: str | None = None
    content_type: str | None = None
    confidence: float = 0.0
    candidates: list[dict] = field(default_factory=list)
    ai: dict | None = None
    reason: str = ""
    overrides: dict = field(default_factory=dict)   # manual field overrides (season, quality...)


class IdentificationPipeline:
    def __init__(self, settings: Settings, db: Any, catalog: Catalog, lookup: TitleService,
                 ai: AIIdentifier | None = None):
        self.s = settings
        self.db = db
        self.catalog = catalog
        self.lookup = lookup
        self.ai = ai

    # ---- helpers --------------------------------------------------------------------
    def parse(self, file_name: str | None, caption: str | None) -> ParsedName:
        primary = parse_filename(file_name or "")
        if caption and (not primary.title or primary.content_type == "unknown" or primary.confidence < 0.6):
            cap = parse_filename(caption.splitlines()[0] if caption else "")
            if cap.title and cap.confidence >= primary.confidence:
                return merge_parsed(cap, primary) if cap.confidence > primary.confidence else merge_parsed(primary, cap)
            return merge_parsed(primary, cap)
        return primary

    async def _manual_for_file(self, file_unique_id: str | None, title_key: str) -> dict | None:
        if file_unique_id:
            doc = await self.db[dbm.CORRECTIONS].find_one(
                {"file_unique_id": file_unique_id, "active": {"$ne": False}}, sort=[("created_at", -1)])
            if doc:
                return doc
        return await self.db[dbm.CORRECTIONS].find_one(
            {"title_key": title_key, "active": {"$ne": False}}, sort=[("created_at", -1)])

    async def _search_all(self, q: MatchQuery) -> list[Candidate]:
        ranked = await self.lookup.search(q)
        if not ranked or ranked[0].score < self.s.match_auto_threshold:
            # year may be part of the title ("Blade Runner 2049"), or a wrong year: retry variants
            variants: list[MatchQuery] = []
            if q.year:
                variants.append(MatchQuery(f"{q.title} {q.year}", None, q.kind, q.alt_titles))
                variants.append(MatchQuery(q.title, None, q.kind, q.alt_titles))
            if q.kind:
                variants.append(MatchQuery(q.title, q.year, None, q.alt_titles))
            seen = {c.imdb_id: c for c in ranked}
            for v in variants:
                for c in await self.lookup.search(v):
                    seen.setdefault(c.imdb_id, c)
            ranked = rank_candidates(q, list(seen.values()))
        return ranked

    # ---- main entry -----------------------------------------------------------------------
    async def identify(self, file_name: str | None, caption: str | None, file_unique_id: str | None = None,
                       use_ai: bool = True) -> Identification:
        """Identify one file. Manual per-file overrides persist across re-indexing."""
        res = await self._identify(file_name, caption, file_unique_id, use_ai)
        if file_unique_id:
            doc = await self.db[dbm.CORRECTIONS].find_one(
                {"file_unique_id": file_unique_id, "active": {"$ne": False}, "overrides": {"$exists": True}},
                sort=[("created_at", -1)])
            if doc and doc.get("overrides"):
                res.overrides = {**doc["overrides"], **res.overrides}
        return res

    async def _identify(self, file_name: str | None, caption: str | None, file_unique_id: str | None,
                        use_ai: bool) -> Identification:
        parsed = self.parse(file_name, caption)
        key = parsed.title_key

        manual = await self._manual_for_file(file_unique_id, key)
        if manual and manual.get("imdb_id"):
            cand = await self.lookup.get(manual["imdb_id"]) if manual.get("kind") is None else None
            if manual.get("kind"):
                cand = Candidate(imdb_id=manual["imdb_id"], title=manual.get("title") or parsed.title,
                                 year=manual.get("year"), kind=manual["kind"], genres=manual.get("genres") or [])
            if cand:
                title_doc = await self.catalog.upsert_title(cand, manual=True)
                return Identification("identified", "manual", parsed, title_id=cand.imdb_id,
                                      content_type=cand.kind, confidence=1.0, reason="manual correction",
                                      overrides=manual.get("overrides") or {})

        if not parsed.title:
            return Identification("unidentified", "none", parsed, reason="no title could be parsed")

        # Group decisions: many files share one title; decide once.
        decision = await self.db[dbm.TITLE_DECISIONS].find_one({"title_key": key})
        if decision and decision.get("status") == "identified":
            cand = await self.lookup.get(decision["imdb_id"]) if decision.get("kind") is None else Candidate(
                imdb_id=decision["imdb_id"], title=decision["title"], year=decision.get("year"), kind=decision["kind"])
            if cand:
                await self.catalog.upsert_title(cand)
                return Identification("identified", decision.get("method", "tmdb"), parsed, title_id=cand.imdb_id,
                                      content_type=cand.kind, confidence=decision.get("confidence", 0.9),
                                      reason="reused group decision")

        q = MatchQuery(parsed.title, parsed.year, parsed.content_type if parsed.content_type != "unknown" else None)
        try:
            ranked = await self._search_all(q)
        except LookupUnavailable as exc:
            return Identification("pending_review", "none", parsed,
                                  reason=f"TMDB unavailable: {str(exc)[:120]}")

        status, best, why = decide(ranked, self.s.match_auto_threshold, self.s.match_review_threshold)
        method = "tmdb"
        ai_dict = None

        if status != "auto" and use_ai and self.ai is not None and await self.ai.available():
            ai_res = await self.ai.normalise(file_name or "", caption, parsed)
            if ai_res:
                ai_dict = ai_res.to_dict()
                q2 = MatchQuery(ai_res.title, ai_res.year or parsed.year,
                                ai_res.content_type or q.kind, [*ai_res.alt_titles, *([ai_res.original_title] if ai_res.original_title else [])])
                try:
                    ranked2 = await self._search_all(q2)
                except LookupUnavailable:
                    ranked2 = []
                s2, b2, w2 = decide(ranked2, self.s.match_auto_threshold, self.s.match_review_threshold)
                # The AI title must be verified by TMDB AND agree with what the file says.
                agrees = bool(b2) and (
                    b2.title_sim >= 0.9 or _loosely_agrees(parsed.title, b2))
                if b2 and (b2.score > (best.score if best else 0)):
                    ranked, best, why = ranked2, b2, w2
                    status = s2 if (s2 != "auto" or agrees) else "review"
                    method = "ai+tmdb"
                if ai_res.season is not None and parsed.season is None:
                    parsed.season = ai_res.season
                if ai_res.episode is not None and parsed.episode is None:
                    parsed.episode = ai_res.episode
                if ai_res.episode_title:
                    parsed.__dict__.setdefault("_episode_title", ai_res.episode_title)
                for code in ai_res.languages:
                    if code not in parsed.languages:
                        parsed.languages.append(code)
                if ai_res.content_type and parsed.content_type == "unknown":
                    parsed.content_type = ai_res.content_type

        cands = [c.to_dict() for c in ranked[:8]]
        if status == "auto" and best:
            await self.catalog.upsert_title(best)
            await self.db[dbm.TITLE_DECISIONS].update_one(
                {"title_key": key},
                {"$set": {"title_key": key, "status": "identified", "imdb_id": best.imdb_id, "title": best.title,
                          "year": best.year, "kind": best.kind, "method": method, "confidence": best.score,
                          "updated_at": now()}}, upsert=True)
            return Identification("identified", method, parsed, title_id=best.imdb_id, content_type=best.kind,
                                  confidence=best.score, candidates=cands, ai=ai_dict, reason=why)
        if status == "review":
            return Identification("pending_review", method, parsed, content_type=best.kind if best else None,
                                  confidence=best.score if best else 0.0, candidates=cands, ai=ai_dict, reason=why)
        if (self.s.auto_local_collections and status != "review" and parsed.title and parsed.confidence >= 0.6
                and parsed.content_type in ("movie", "series")):
            # TMDB has no confident match and nothing is waiting for a human decision: keep the file under one
            # stable local collection per title so every language/quality of it is grouped together.
            doc = await self.catalog.ensure_local_collection(parsed.content_type, parsed.title, parsed.year)
            return Identification("identified", "local", parsed, title_id=doc["_id"], content_type=parsed.content_type,
                                  confidence=parsed.confidence, candidates=cands, ai=ai_dict,
                                  reason="no TMDB match; grouped in a local collection")
        return Identification("unidentified", method, parsed, candidates=cands, ai=ai_dict,
                              confidence=best.score if best else 0.0, reason=why)

    # ---- applying a decision to a file (used by indexer and admin tools) -----------------------------
    async def apply_to_file(self, file_id: Any, ident: Identification, extra_overrides: dict | None = None) -> None:
        p = ident.parsed
        ov = {**ident.overrides, **(extra_overrides or {})}
        ai_langs = bool(ident.ai and ident.ai.get("languages") and not _file_langs(p, ident))
        update: dict = {
            "status": ident.status,
            "identification": {"method": ident.method, "confidence": ident.confidence, "reason": ident.reason,
                               "at": now()},
            "title_key": p.title_key,
            "parsed": p.to_dict(),
            "languages": ov.get("languages") or p.languages or ["xx"],
            "languages_source": ("manual" if ov.get("languages") else "ai" if ai_langs else
                                 "filename" if p.languages else "unknown"),
            "subtitles": p.subtitles,
            "quality": ov.get("quality") or p.quality,
            "resolution": p.resolution,
            "source": p.source, "video_codec": p.video_codec, "audio_codec": p.audio_codec,
            "hdr": p.hdr, "edition": p.edition, "release_group": p.release_group,
            "is_cam": p.is_cam, "multi_audio": p.multi_audio,
            "season": ov.get("season", p.season), "episode": ov.get("episode", p.episode),
            "episode_end": p.episode_end,
            "is_season_pack": p.is_season_pack, "is_complete_series": p.is_complete_series,
            "updated_at": now(),
        }
        update["collection_id"] = ident.title_id          # relationship: file -> its collection (loc_/tt id)
        if ident.title_id:
            update["title_id"] = ident.title_id
            update["content_type"] = ident.content_type
            if ident.content_type == "movie":
                update.update({"season": None, "episode": None, "is_season_pack": False, "is_complete_series": False})
        else:
            update["title_id"] = None
            update["content_type"] = p.content_type
        if ov.get("episode_title") or getattr(p, "_episode_title", None):
            update["episode_title"] = ov.get("episode_title") or getattr(p, "_episode_title")
        await self.db[dbm.FILES].update_one({"_id": file_id}, {"$set": update})

        # Episode titles from TMDB (best effort, only for verified series)
        if ident.status == "identified" and ident.content_type == "series" and update.get("season") is not None \
                and update.get("episode") is not None and "episode_title" not in update:
            try:
                info = await self.lookup.episode(ident.title_id, update["season"], update["episode"])
                if info and info.get("title"):
                    await self.db[dbm.FILES].update_one({"_id": file_id}, {"$set": {"episode_title": info["title"]}})
            except Exception:
                pass

        if ident.status == "identified":
            await self.db[dbm.QUEUE].delete_many({"file_id": file_id})
        else:
            await self.db[dbm.QUEUE].update_one(
                {"file_id": file_id},
                {"$set": {"file_id": file_id, "status": "pending", "title_key": p.title_key,
                          "candidates": ident.candidates, "ai": ident.ai, "reason": ident.reason,
                          "parsed": p.to_dict(), "updated_at": now()},
                 "$setOnInsert": {"created_at": now()}}, upsert=True)
        if ident.title_id:
            await self.catalog.refresh_title_summary(ident.title_id)
            if ident.content_type == "series":
                await self.catalog.sync_seasons_episodes(ident.title_id)


def _file_langs(p: ParsedName, ident: Identification) -> list[str]:
    """Languages the file name itself states (not added by the AI)."""
    ai = set((ident.ai or {}).get("languages") or [])
    return [c for c in p.languages if c not in ai]


def _loosely_agrees(file_title: str, cand: Candidate) -> bool:
    a = match_key(file_title)
    names = [match_key(cand.title), *[match_key(x) for x in cand.alt_titles]]
    return any(a and n and (a in n or n in a) for n in names)
