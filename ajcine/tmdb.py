"""TMDB API client and the poster service. Images are stored as URLs only; nothing is downloaded or saved to disk.

This product uses the TMDB API but is not endorsed or certified by TMDB.
Title identification lives in `matching/service.py` (it uses this client); this module also fills in artwork URLs.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db as dbm
from .config import Settings
from .parsing.text import match_key

log = logging.getLogger("ajcine.tmdb")
URL_RE = re.compile(r"^https://[^\s]{4,500}$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def poster_src(poster: str | None) -> str | None:
    """Browser-ready address for a stored poster: URLs as they are, legacy local file names via /posters."""
    if not poster:
        return None
    return poster if poster.startswith("https://") else f"/posters/{poster}"


def valid_image_url(url: str | None) -> bool:
    return bool(url and URL_RE.match(url.strip()))


class TmdbError(RuntimeError):
    """A strict TMDB call failed (not configured, rejected key, rate limit, server or network trouble)."""


class TmdbClient:
    def __init__(self, settings: Settings, session: Any | None = None):
        self.s = settings
        self._session = session
        self._lock = asyncio.Semaphore(4)

    @property
    def enabled(self) -> bool:
        return self.s.tmdb_enabled

    async def _sess(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, path: str, params: dict | None = None, strict: bool = False) -> dict | None:
        """GET a TMDB path. Quietly returns None on trouble, or with `strict=True` raises TmdbError
        (a 404 is "not found" and returns None either way)."""
        def fail(msg: str):
            if strict:
                raise TmdbError(msg)
            return None
        if not self.enabled:
            return fail("TMDB is not configured (set TMDB_API_KEY or TMDB_READ_TOKEN)")
        params = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.s.tmdb_read_token:
            headers["Authorization"] = f"Bearer {self.s.tmdb_read_token}"
        else:
            params["api_key"] = self.s.tmdb_api_key
        sess = await self._sess()
        last = "no response"
        for attempt in range(3):
            try:
                async with self._lock, sess.get(self.s.tmdb_api_base + path, params=params, headers=headers) as r:
                    if r.status == 429:
                        last = "rate limited by TMDB"
                        await asyncio.sleep(min(10, float(r.headers.get("Retry-After", "2"))))
                        continue
                    if r.status >= 500:
                        last = f"TMDB server error {r.status}"
                        await asyncio.sleep(1 + attempt)
                        continue
                    if r.status == 404:
                        return None
                    if r.status != 200:
                        log.warning("TMDB %s -> HTTP %s", path.split("?")[0], r.status)
                        return fail("TMDB rejected the API key" if r.status in (401, 403) else f"TMDB returned HTTP {r.status}")
                    return await r.json()
            except TmdbError:
                raise
            except (asyncio.TimeoutError, OSError) as exc:
                last = f"network error ({type(exc).__name__})"
                log.warning("TMDB request failed: %s", type(exc).__name__)
                await asyncio.sleep(1 + attempt)
            except Exception as exc:  # aiohttp.ClientError and friends
                log.warning("TMDB request failed: %s", type(exc).__name__)
                return fail(f"TMDB request failed ({type(exc).__name__})")
        return fail(last)

    def image(self, path: str | None, size: str) -> str | None:
        return f"{self.s.tmdb_image_base}{size}{path}" if path and path.startswith("/") else None

    async def lookup(self, title: dict) -> dict | None:
        """Artwork for a catalog title: {'tmdb_id', 'poster', 'backdrop'} or None when TMDB has no confident match."""
        kind = title.get("kind")
        tid = title["_id"]
        hit = None
        if tid.startswith("tt"):
            data = await self._get(f"/find/{tid}", {"external_source": "imdb_id"})
            if data:
                hit = next(iter(data.get("tv_results" if kind == "series" else "movie_results") or []), None)
        else:
            hit = await self._search(title)
        if not hit or not hit.get("poster_path"):
            return None
        return {"tmdb_id": hit.get("id"), "poster": self.image(hit.get("poster_path"), self.s.tmdb_poster_size),
                "backdrop": self.image(hit.get("backdrop_path"), self.s.tmdb_backdrop_size)}

    async def _search(self, title: dict) -> dict | None:
        """Local titles: accept a result only when its name matches exactly (and the year is close)."""
        kind, name, year = title.get("kind"), title.get("title") or "", title.get("year")
        movie = kind != "series"
        params: dict = {"query": name, "include_adult": "false"}
        if year:
            params["primary_release_year" if movie else "first_air_date_year"] = year
        data = await self._get("/search/movie" if movie else "/search/tv", params)
        want = match_key(name)
        for r in (data or {}).get("results", [])[:8]:
            names = {match_key(r.get("title") or r.get("name")), match_key(r.get("original_title") or r.get("original_name"))}
            if want not in names:
                continue
            ry = int(((r.get("release_date") or r.get("first_air_date") or "0000")[:4]) or 0)
            if year and ry and abs(ry - int(year)) > 1:
                continue
            return r
        return None


class PosterService:
    """Fills in missing posters from TMDB. A poster set by a person is never overwritten."""

    def __init__(self, settings: Settings, db: Any, catalog: Any, client: TmdbClient | None = None):
        self.s, self.db, self.catalog = settings, db, catalog
        self.tmdb = client or TmdbClient(settings)
        self._stop = False

    async def refresh_title(self, title_id: str, force: bool = False) -> str:
        """Returns: set | none | manual | disabled | missing"""
        if not self.tmdb.enabled:
            return "disabled"
        t = await self.catalog.get_title(title_id)
        if not t:
            return "missing"
        if t.get("poster") and t.get("poster_source") == "manual" and not force:
            return "manual"
        found = await self.tmdb.lookup(t)
        coll = dbm.SERIES if t.get("kind") == "series" else dbm.MOVIES
        if not found or not found.get("poster"):
            await self.db[coll].update_one({"_id": title_id}, {"$set": {"tmdb_checked_at": now()}})
            return "none"
        await self.catalog.set_poster(title_id, found["poster"], "tmdb", found.get("backdrop"))
        await self.db[coll].update_one({"_id": title_id}, {"$set": {"tmdb_id": found.get("tmdb_id"), "tmdb_checked_at": now()}})
        return "set"

    async def pending(self, limit: int = 10) -> list[str]:
        cutoff = now() - timedelta(days=self.s.tmdb_recheck_days)
        out: list[str] = []
        flt = {"file_count": {"$gt": 0}, "$or": [{"poster": None}, {"poster": {"$exists": False}}],
               "$and": [{"$or": [{"tmdb_checked_at": {"$exists": False}}, {"tmdb_checked_at": None},
                                 {"tmdb_checked_at": {"$lt": cutoff}}]}]}
        for coll in (dbm.MOVIES, dbm.SERIES):
            async for d in self.db[coll].find(flt, {"_id": 1}).limit(limit):
                out.append(d["_id"])
        return out[:limit]

    async def run(self, interval: float = 30.0) -> None:
        if not self.tmdb.enabled:
            return
        log.info("TMDB poster service started")
        while not self._stop:
            try:
                for tid in await self.pending(10):
                    await self.refresh_title(tid)
                    await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("poster service error: %s", type(exc).__name__)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self._stop = True
