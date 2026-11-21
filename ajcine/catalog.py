"""Catalog service: titles, seasons, episodes, files and their grouping.

Hierarchy:  Series -> Seasons -> Episodes -> Languages -> Qualities -> Files
Movies are titles with no seasons. Languages and qualities are never stored
as a separate truth: they are always derived from real, non-deleted indexed
files, so the UI can only ever offer what actually exists.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from . import db as dbm
from .matching.models import Candidate
from .parsing.languages import lang_name
from .parsing.text import tokens_for_index, match_key

log = logging.getLogger("ajcine.catalog")

QUALITY_ORDER = {"2160p": 0, "1440p": 1, "1080p": 2, "720p": 3, "576p": 4, "540p": 5, "480p": 6, "360p": 7, "240p": 8, "SD": 9}


def now() -> datetime:
    return datetime.now(timezone.utc)


def quality_sort_key(q: str | None) -> int:
    return QUALITY_ORDER.get(q or "", 99)


def human_size(n: int | None) -> str:
    if not n:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.2f} {unit}"
        size /= 1024
    return "?"


def coll_for(kind: str) -> str:
    return dbm.SERIES if kind == "series" else dbm.MOVIES


LOCAL_PREFIX = "loc_"


def is_local_id(title_id: str | None) -> bool:
    return bool(title_id and str(title_id).startswith(LOCAL_PREFIX))


def local_id(kind: str, title: str, year: int | None) -> str:
    """Deterministic id of a local collection: the same kind + normalised title + year always gives the same id.
    The formula is unchanged from earlier releases so ids that already exist stay valid."""
    return LOCAL_PREFIX + hashlib.sha1(f"{kind}:{match_key(title)}:{year or ''}".encode()).hexdigest()[:10]


class Catalog:
    def __init__(self, db: Any, cache: Any):
        self.db = db
        self.cache = cache

    # ---- cache versioning ---------------------------------------------------
    async def version(self) -> int:
        return int(await self.cache.get("catalog_version") or 0)

    async def bump(self) -> None:
        await self.cache.set("catalog_version", await self.version() + 1, ttl=0)

    # ---- titles -------------------------------------------------------------------
    async def get_title(self, title_id: str) -> dict | None:
        doc = await self.db[dbm.MOVIES].find_one({"_id": title_id})
        if doc:
            return doc
        return await self.db[dbm.SERIES].find_one({"_id": title_id})

    async def upsert_title(self, c: Candidate, manual: bool = False, poster: str | None = None) -> dict:
        coll = self.db[coll_for(c.kind)]
        other = self.db[dbm.MOVIES if c.kind == "series" else dbm.SERIES]
        existing = await coll.find_one({"_id": c.imdb_id})
        alt = list(dict.fromkeys([*(existing or {}).get("alt_titles", []), *c.alt_titles]))[:40]
        fields = {
            "title": (existing or {}).get("title") if (existing or {}).get("manual") and not manual else c.title,
            "original_title": c.original_title or (existing or {}).get("original_title"),
            "alt_titles": alt,
            "year": c.year if c.year is not None else (existing or {}).get("year"),
            "kind": c.kind,
            "rating": c.rating if c.rating is not None else (existing or {}).get("rating", (existing or {}).get("imdb_rating")),
            "votes": c.votes if c.votes is not None else (existing or {}).get("votes"),
            "genres": c.genres or (existing or {}).get("genres", []),
            "runtime": c.runtime or (existing or {}).get("runtime"),
            "overview": c.plot or (existing or {}).get("overview"),
            "updated_at": now(),
        }
        if existing and existing.get("manual") and not manual:
            # manual corrections win over automatic data
            for k in ("title", "year", "genres", "overview", "original_title"):
                if existing.get(k) not in (None, "", []):
                    fields[k] = existing[k]
        if manual:
            fields["manual"] = True
        if poster:
            fields["poster"] = poster
        fields["match_key"] = match_key(fields["title"])
        fields["search_tokens"] = tokens_for_index(fields["title"], fields["original_title"], *fields["alt_titles"])
        fields["popularity"] = float(fields.get("votes") or 0)
        await coll.update_one(
            {"_id": c.imdb_id},
            {"$set": fields, "$setOnInsert": {"created_at": now(), "file_count": 0, "languages": [], "qualities": []}},
            upsert=True,
        )
        await other.delete_one({"_id": c.imdb_id})  # a title cannot be both
        await self.bump()
        return await coll.find_one({"_id": c.imdb_id})

    # ---- local collections ------------------------------------------------------------------------
    async def resolve_local_id(self, kind: str, title: str, year: int | None) -> str:
        """The collection id for a movie/series that TMDB does not know.

        One collection per title: all languages and qualities of the same movie land in the same collection,
        and a file whose name has no year joins the existing collection for that title instead of starting a
        second one. Two different years of the same name (a remake) stay separate collections.
        """
        key = match_key(title)
        if not key:
            return local_id(kind, title, year)
        coll = self.db[coll_for(kind)]
        found = [d async for d in coll.find({"_id": {"$regex": "^" + LOCAL_PREFIX}, "match_key": key},
                                            {"year": 1, "file_count": 1})]
        if year is not None:
            for d in found:
                if d.get("year") == year:
                    return d["_id"]
            yearless = [d for d in found if d.get("year") is None]
            if len(yearless) == 1:
                await coll.update_one({"_id": yearless[0]["_id"]}, {"$set": {"year": year, "updated_at": now()}})
                return yearless[0]["_id"]
            return local_id(kind, title, year)
        if len(found) == 1:
            return found[0]["_id"]
        for d in found:
            if d.get("year") is None:
                return d["_id"]
        return local_id(kind, title, None)

    async def ensure_local_collection(self, kind: str, title: str, year: int | None, manual: bool = False) -> dict:
        tid = await self.resolve_local_id(kind, title, year)
        existing = await self.get_title(tid)
        if existing:
            if year is not None and existing.get("year") is None:
                await self.db[coll_for(kind)].update_one({"_id": tid}, {"$set": {"year": year}})
                existing["year"] = year
            return existing
        return await self.upsert_title(Candidate(imdb_id=tid, title=title, year=year, kind=kind), manual=manual)

    async def set_poster(self, title_id: str, poster: str | None, source: str | None = None,
                         backdrop: str | None = None) -> bool:
        """Store a poster as a URL (never a file). `source` is 'manual' or 'tmdb'."""
        fields: dict = {"poster": poster, "poster_source": source if poster else None, "updated_at": now()}
        if backdrop is not None or poster is None:
            fields["backdrop"] = backdrop
        for coll in (dbm.MOVIES, dbm.SERIES):
            r = await self.db[coll].update_one({"_id": title_id}, {"$set": fields})
            if r.matched_count:
                await self.bump()
                return True
        return False

    async def refresh_title_summary(self, title_id: str) -> None:
        """Recompute file_count/languages/qualities/last_indexed_at from real files."""
        base = {"title_id": title_id, "deleted": {"$ne": True}, "missing": {"$ne": True}, "status": "identified"}
        count = await self.db[dbm.FILES].count_documents(base)
        langs = await self.db[dbm.FILES].distinct("languages", base)
        quals = await self.db[dbm.FILES].distinct("quality", base)
        last = await self.db[dbm.FILES].find_one(base, sort=[("indexed_at", -1)], projection={"indexed_at": 1})
        upd = {
            "file_count": count,
            "languages": sorted(l for l in langs if l),
            "qualities": sorted((q for q in quals if q), key=quality_sort_key),
        }
        if last:
            upd["last_indexed_at"] = last.get("indexed_at")
        for coll in (dbm.MOVIES, dbm.SERIES):
            r = await self.db[coll].update_one({"_id": title_id}, {"$set": upd})
            if r.matched_count:
                break
        await self.bump()

    async def sync_seasons_episodes(self, series_id: str) -> None:
        """Maintain seasons/episodes collections from the files that exist."""
        base = {"title_id": series_id, "deleted": {"$ne": True}, "status": "identified"}
        pipeline = [
            {"$match": {**base, "season": {"$ne": None}}},
            {"$group": {"_id": {"s": "$season", "e": "$episode"}, "episode_title": {"$first": "$episode_title"}}},
        ]
        seen_seasons: set[int] = set()
        seen_eps: set[tuple[int, int]] = set()
        async for row in self.db[dbm.FILES].aggregate(pipeline):
            s, e = row["_id"]["s"], row["_id"]["e"]
            seen_seasons.add(s)
            if e is not None:
                seen_eps.add((s, e))
                await self.db[dbm.EPISODES].update_one(
                    {"series_id": series_id, "season": s, "episode": e},
                    {"$set": {"title": row.get("episode_title"), "updated_at": now()},
                     "$setOnInsert": {"created_at": now()}}, upsert=True)
        for s in seen_seasons:
            await self.db[dbm.SEASONS].update_one(
                {"series_id": series_id, "season": s},
                {"$set": {"updated_at": now()}, "$setOnInsert": {"created_at": now()}}, upsert=True)
        # prune rows that no longer have files
        async for ep in self.db[dbm.EPISODES].find({"series_id": series_id}, {"season": 1, "episode": 1}):
            if (ep["season"], ep["episode"]) not in seen_eps:
                await self.db[dbm.EPISODES].delete_one({"_id": ep["_id"]})
        await self.db[dbm.SEASONS].delete_many({"series_id": series_id, "season": {"$nin": list(seen_seasons)}})

    # ---- navigation queries ------------------------------------------------------------------
    def _file_filter(self, title_id: str, season: int | None = None, episode: int | None = None,
                     language: str | None = None, quality: str | None = None, pack: str | None = None) -> dict:
        f: dict = {"title_id": title_id, "deleted": {"$ne": True}, "missing": {"$ne": True}, "status": "identified"}
        if pack == "season":
            f.update({"season": season, "is_season_pack": True})
        elif pack == "series":
            f["is_complete_series"] = True
        else:
            if season is not None:
                f["season"] = season
            if episode is not None:
                f["episode"] = episode
                f["is_season_pack"] = {"$ne": True}
                f["is_complete_series"] = {"$ne": True}
        if language:
            f["languages"] = language
        if quality:
            f["quality"] = quality
        return f

    async def languages_for(self, title_id: str, season: int | None = None, episode: int | None = None,
                            pack: str | None = None) -> list[dict]:
        f = self._file_filter(title_id, season, episode, pack=pack)
        pipeline = [{"$match": f}, {"$unwind": "$languages"},
                    {"$group": {"_id": "$languages", "files": {"$sum": 1}}}, {"$sort": {"files": -1}}]
        out = []
        async for r in self.db[dbm.FILES].aggregate(pipeline):
            out.append({"code": r["_id"], "name": lang_name(r["_id"]), "files": r["files"]})
        return out

    async def qualities_for(self, title_id: str, language: str, season: int | None = None,
                            episode: int | None = None, pack: str | None = None) -> list[dict]:
        f = self._file_filter(title_id, season, episode, language, pack=pack)
        pipeline = [{"$match": f}, {"$group": {"_id": "$quality", "files": {"$sum": 1}, "best_size": {"$max": "$size"}}}]
        rows = [r async for r in self.db[dbm.FILES].aggregate(pipeline)]
        rows.sort(key=lambda r: quality_sort_key(r["_id"]))
        return [{"quality": r["_id"] or "Unknown", "raw": r["_id"], "files": r["files"], "size": r["best_size"]} for r in rows]

    async def files_for(self, title_id: str, language: str | None, quality: str | None, season: int | None = None,
                        episode: int | None = None, pack: str | None = None) -> list[dict]:
        f = self._file_filter(title_id, season, episode, language, quality, pack=pack)
        cur = self.db[dbm.FILES].find(f).sort([("size", -1)]).limit(25)
        return [d async for d in cur]

    async def seasons_of(self, series_id: str) -> list[dict]:
        base = {"title_id": series_id, "deleted": {"$ne": True}, "missing": {"$ne": True}, "status": "identified",
                "season": {"$ne": None}}
        pipeline = [{"$match": base}, {"$group": {
            "_id": "$season",
            "episodes": {"$addToSet": "$episode"},
            "pack": {"$max": {"$cond": ["$is_season_pack", 1, 0]}},
        }}, {"$sort": {"_id": 1}}]
        out = []
        async for r in self.db[dbm.FILES].aggregate(pipeline):
            eps = sorted(e for e in r["episodes"] if e is not None)
            out.append({"season": r["_id"], "episodes": len(eps), "has_pack": bool(r["pack"])})
        return out

    async def episodes_of(self, series_id: str, season: int) -> list[dict]:
        base = {"title_id": series_id, "season": season, "deleted": {"$ne": True}, "missing": {"$ne": True},
                "status": "identified", "episode": {"$ne": None}, "is_season_pack": {"$ne": True}}
        pipeline = [{"$match": base}, {"$group": {"_id": "$episode", "title": {"$first": "$episode_title"},
                                                   "files": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
        return [{"episode": r["_id"], "title": r.get("title"), "files": r["files"]}
                async for r in self.db[dbm.FILES].aggregate(pipeline)]

    async def has_complete_pack(self, series_id: str) -> bool:
        return bool(await self.db[dbm.FILES].count_documents(
            self._file_filter(series_id, pack="series"), limit=1))

    async def has_season_pack(self, series_id: str, season: int) -> bool:
        return bool(await self.db[dbm.FILES].count_documents(
            self._file_filter(series_id, season, pack="season"), limit=1))

    async def missing_episodes(self, series_id: str) -> dict[int, list[int]]:
        """Gaps in 1..max(episode) per season, based on indexed single episodes."""
        out: dict[int, list[int]] = {}
        for s in await self.seasons_of(series_id):
            have = {e["episode"] for e in await self.episodes_of(series_id, s["season"])}
            if have:
                gaps = [n for n in range(1, max(have) + 1) if n not in have]
                if gaps:
                    out[s["season"]] = gaps
        return out

    async def duplicate_groups(self, limit: int = 100) -> list[dict]:
        """Same title/season/episode/language/quality present more than once."""
        pipeline = [
            {"$match": {"deleted": {"$ne": True}, "status": "identified", "title_id": {"$ne": None}}},
            {"$unwind": "$languages"},
            {"$group": {"_id": {"t": "$title_id", "s": "$season", "e": "$episode", "l": "$languages", "q": "$quality"},
                        "files": {"$push": {"id": "$_id", "name": "$file_name", "size": "$size"}}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$sort": {"n": -1}}, {"$limit": limit},
        ]
        return [{"key": r["_id"], "count": r["n"], "files": r["files"]} async for r in self.db[dbm.FILES].aggregate(pipeline)]

    # ---- merge / delete ----------------------------------------------------------------------------
    async def merge_titles(self, src_id: str, dst_id: str) -> int:
        """Move all files from src to dst, then drop src. Returns files moved."""
        if src_id == dst_id:
            return 0
        dst = await self.get_title(dst_id)
        if not dst:
            raise ValueError("destination title does not exist")
        res = await self.db[dbm.FILES].update_many(
            {"title_id": src_id}, {"$set": {"title_id": dst_id, "content_type": dst["kind"], "updated_at": now()}})
        src = await self.get_title(src_id)
        if src:
            alt = list(dict.fromkeys([*dst.get("alt_titles", []), src["title"], *src.get("alt_titles", [])]))[:40]
            await self.db[coll_for(dst["kind"])].update_one(
                {"_id": dst_id}, {"$set": {"alt_titles": alt,
                                           "search_tokens": tokens_for_index(dst["title"], dst.get("original_title"), *alt)}})
        for coll in (dbm.MOVIES, dbm.SERIES):
            await self.db[coll].delete_one({"_id": src_id})
        await self.db[dbm.SEASONS].delete_many({"series_id": src_id})
        await self.db[dbm.EPISODES].delete_many({"series_id": src_id})
        await self.refresh_title_summary(dst_id)
        if dst["kind"] == "series":
            await self.sync_seasons_episodes(dst_id)
        return res.modified_count

    async def prune_title_if_empty(self, title_id: str) -> None:
        n = await self.db[dbm.FILES].count_documents({"title_id": title_id, "deleted": {"$ne": True}}, limit=1)
        if not n:
            for coll in (dbm.MOVIES, dbm.SERIES):
                doc = await self.db[coll].find_one({"_id": title_id})
                if doc and not doc.get("manual"):
                    await self.db[coll].delete_one({"_id": title_id})
            await self.db[dbm.SEASONS].delete_many({"series_id": title_id})
            await self.db[dbm.EPISODES].delete_many({"series_id": title_id})
            await self.bump()

    # ---- stats ----------------------------------------------------------------------------------------
    async def stats(self) -> dict:
        d = self.db
        files = await d[dbm.FILES].count_documents({"deleted": {"$ne": True}})
        return {
            "files": files,
            "files_identified": await d[dbm.FILES].count_documents({"deleted": {"$ne": True}, "status": "identified"}),
            "files_pending_review": await d[dbm.FILES].count_documents({"deleted": {"$ne": True}, "status": "pending_review"}),
            "files_unidentified": await d[dbm.FILES].count_documents({"deleted": {"$ne": True}, "status": "unidentified"}),
            "files_missing": await d[dbm.FILES].count_documents({"missing": True}),
            "movies": await d[dbm.MOVIES].count_documents({}),
            "series": await d[dbm.SERIES].count_documents({}),
            "seasons": await d[dbm.SEASONS].count_documents({}),
            "episodes": await d[dbm.EPISODES].count_documents({}),
            "users": await d[dbm.USERS].count_documents({}),
            "searches": await d[dbm.SEARCH_HISTORY].count_documents({}),
            "deliveries": await d[dbm.DELIVERIES].count_documents({"status": {"$ne": "streamed"}}),
            "streams": await d[dbm.DELIVERIES].count_documents({"status": "streamed"}),
            "review_queue": await d[dbm.QUEUE].count_documents({"status": "pending"}),
            "jobs_failed": await d[dbm.JOBS].count_documents({"status": "failed"}),
            "jobs_running": await d[dbm.JOBS].count_documents({"status": {"$in": ["running", "queued"]}}),
        }
