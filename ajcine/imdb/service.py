
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import Settings
from .. import db as dbm
from ..parsing.text import match_key
from .models import Candidate, MatchQuery, kind_of, rank_candidates

log = logging.getLogger("ajcine.imdb")

_ID_RE = re.compile(r"^tt\d{7,10}$")


def valid_imdb_id(value: str | None) -> bool:
    return bool(value and _ID_RE.match(value))


def to_imdb_id(movie_id: Any) -> str:
    return "tt" + str(movie_id).lstrip("t").zfill(7)


def to_movie_id(imdb_id: str) -> str:
    return imdb_id[2:].lstrip("0") or "0"


def _first(val: Any) -> Any:
    if isinstance(val, (list, tuple)):
        return val[0] if val else None
    return val


class ImdbService:
    def __init__(self, settings: Settings, db: Any, cache: Any | None = None):
        self.settings = settings
        self.db = db
        self.cache = cache
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cinemagoer")
        self._ia: Any = None
        self._ia_error: str | None = None
        self._generation = 0

    # ---- lifecycle ---------------------------------------------------------
    def _open(self) -> Any:
        if self._ia is not None:
            return self._ia
        import imdb  # imported lazily: Cinemagoer is optional until used

        factory = getattr(imdb, "Cinemagoer", None) or imdb.IMDb
        try:
            self._ia = factory("s3", uri=self.settings.imdb_db_uri)
            self._ia_error = None
        except Exception as exc:
            self._ia_error = f"{type(exc).__name__}: {exc}"
            raise
        return self._ia

    def _close(self) -> None:
        ia, self._ia = self._ia, None
        if ia is not None:
            try:
                close = getattr(ia, "close", None)
                if close:
                    close()
            except Exception:
                pass

    async def reload(self) -> None:
        """Re-open the database (after a dataset swap)."""
        await self._run(self._close)
        self._generation += 1

    async def close(self) -> None:
        await self._run(self._close)
        self._pool.shutdown(wait=False)

    async def _run(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, lambda: fn(*args))

    # ---- search -------------------------------------------------------------
    def _movie_to_candidate(self, m: Any) -> Candidate | None:
        kind = kind_of(m.get("kind"))
        if kind is None:
            return None
        title = m.get("title") or m.get("long imdb title")
        if not title:
            return None
        alt: list[str] = []
        for a in (m.get("akas") or [])[:40]:
            name = a.get("title") if isinstance(a, dict) else str(a)
            name = re.sub(r"\s*\(.*?\)\s*$", "", str(name or "")).strip()
            if name and name != title and name not in alt:
                alt.append(name)
        alt = alt[:30]
        plot = _first(m.get("plot outline") or m.get("plot") or None)
        if isinstance(plot, str) and "::" in plot:
            plot = plot.split("::")[0]
        runtimes = m.get("runtimes") or []
        try:
            runtime = int(runtimes[0]) if runtimes else None
        except (ValueError, TypeError):
            runtime = None
        return Candidate(
            imdb_id=to_imdb_id(m.movieID),
            title=str(title),
            year=int(m["year"]) if str(m.get("year") or "").isdigit() else None,
            kind=kind,
            original_title=m.get("original title"),
            alt_titles=alt,
            rating=float(m["rating"]) if m.get("rating") else None,
            votes=int(m["votes"]) if m.get("votes") else None,
            genres=[str(g).title() for g in (m.get("genres") or [])],
            runtime=runtime,
            plot=str(plot)[:700] if plot else None,
        )

    def _search_sync(self, title: str, limit: int, detail_top: int) -> list[dict]:
        ia = self._open()
        results = ia.search_movie(title, results=limit) or []
        out: list[Candidate] = []
        for idx, m in enumerate(results):
            if idx < detail_top:
                try:
                    ia.update(m)
                except Exception as exc:  # detail is a bonus, not a requirement
                    log.debug("imdb update failed for %s: %s", getattr(m, "movieID", "?"), exc)
            cand = self._movie_to_candidate(m)
            if cand:
                out.append(cand)
        return [c.to_dict() for c in out]

    async def search(self, q: MatchQuery, limit: int = 12, detail_top: int = 5) -> list[Candidate]:
        """Search IMDb and return ranked candidates for the query."""
        if not q.title.strip():
            return []
        key = "s:" + hashlib.sha1(f"{match_key(q.title)}|{q.year}|{q.kind}".encode()).hexdigest()
        cached = await self.db[dbm.IMDB_CACHE].find_one({"key": key})
        raw: list[dict]
        if cached:
            raw = cached["value"]
        else:
            try:
                raw = await self._run(self._search_sync, q.title, limit, detail_top)
            except Exception as exc:
                log.error("imdb search failed: %s", type(exc).__name__)
                raise ImdbUnavailable(str(exc)) from exc
            if raw:
                await self.db[dbm.IMDB_CACHE].update_one(
                    {"key": key}, {"$set": {"key": key, "value": raw, "created_at": _now()}}, upsert=True)
        cands = [Candidate.from_dict(d) for d in raw]
        for c in cands:  # reset volatile scoring state from cache
            c.score, c.title_sim, c.reasons = 0.0, 0.0, {}
        return rank_candidates(q, cands)

    # ---- details ---------------------------------------------------------------
    def _get_sync(self, imdb_id: str) -> dict | None:
        ia = self._open()
        m = ia.get_movie(to_movie_id(imdb_id))
        if not m:
            return None
        c = self._movie_to_candidate(m)
        return c.to_dict() if c else None

    async def get(self, imdb_id: str) -> Candidate | None:
        if not valid_imdb_id(imdb_id):
            return None
        key = "t:" + imdb_id
        cached = await self.db[dbm.IMDB_CACHE].find_one({"key": key})
        if cached:
            return Candidate.from_dict(cached["value"])
        try:
            d = await self._run(self._get_sync, imdb_id)
        except Exception as exc:
            log.error("imdb get failed: %s", type(exc).__name__)
            raise ImdbUnavailable(str(exc)) from exc
        if d:
            await self.db[dbm.IMDB_CACHE].update_one(
                {"key": key}, {"$set": {"key": key, "value": d, "created_at": _now()}}, upsert=True)
            return Candidate.from_dict(d)
        return None

    def _episode_sync(self, imdb_id: str, season: int, episode: int) -> dict | None:
        ia = self._open()
        series = ia.get_movie(to_movie_id(imdb_id))
        ia.update(series, "episodes")
        eps = (series.get("episodes") or {}).get(season) or {}
        ep = eps.get(episode)
        if not ep:
            return None
        return {"title": ep.get("title"), "year": ep.get("year"), "plot": _first(ep.get("plot"))}

    async def episode(self, imdb_id: str, season: int, episode: int) -> dict | None:
        """Best-effort episode title; returns None when the dataset lacks it."""
        key = f"e:{imdb_id}:{season}:{episode}"
        cached = await self.db[dbm.IMDB_CACHE].find_one({"key": key})
        if cached:
            return cached["value"] or None
        try:
            d = await self._run(self._episode_sync, imdb_id, season, episode)
        except Exception as exc:
            log.debug("episode lookup failed: %s", type(exc).__name__)
            return None
        await self.db[dbm.IMDB_CACHE].update_one(
            {"key": key}, {"$set": {"key": key, "value": d, "created_at": _now()}}, upsert=True)
        return d

    # ---- health -------------------------------------------------------------------
    def _probe_sync(self) -> dict:
        t0 = time.time()
        ia = self._open()
        res = ia.search_movie("The Matrix", results=3) or []
        return {"ok": bool(res), "results": len(res), "latency_ms": int((time.time() - t0) * 1000)}

    async def probe(self) -> dict:
        try:
            return await self._run(self._probe_sync)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


class ImdbUnavailable(RuntimeError):
    """Raised when the local IMDb database cannot be queried."""


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
