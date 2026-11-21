"""Collection maintenance and reporting.

    python -m ajcine.tools.collections report [--id ID] [--kind movie|series] [--json]
    python -m ajcine.tools.collections migrate [--apply]

`migrate` is a dry run unless --apply is given. It never deletes files: it (1) links every file to its
collection (`collection_id`), (2) stores the normalised `match_key` on titles, and (3) merges local (`loc_`)
collections that are really the same movie/series (same normalised title; a missing year is compatible with
a known year) into the oldest/largest one, keeping that collection's existing id.

Developed by AJ APPLICATIONS · Developer: AJWEYA
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .. import db as dbm
from ..catalog import Catalog, human_size, is_local_id, quality_sort_key
from ..parsing.languages import lang_name
from ..parsing.text import match_key

COLLECTION_COLUMNS = ("Collection ID", "Collection", "Type", "Year", "Files", "Languages", "Qualities")
FILE_COLUMNS = ("Season/Episode", "Filename", "Resolution", "Audio", "Codec", "Size", "Source message")


def _se(f: dict) -> str:
    if f.get("is_complete_series"):
        return "complete series"
    if f.get("season") is None:
        return "-"
    if f.get("is_season_pack") or f.get("episode") is None:
        return f"S{f['season']:02d} pack"
    return f"S{f['season']:02d}E{f['episode']:02d}"


def collection_row(t: dict) -> dict:
    """Collection-level metadata only: nothing here describes a single file."""
    return {
        "Collection ID": t["_id"], "Collection": t.get("title"), "Type": t.get("kind"), "Year": t.get("year") or "-",
        "Files": t.get("file_count") or 0,
        "Languages": ", ".join(lang_name(c) for c in t.get("languages") or []) or "-",
        "Qualities": ", ".join(t.get("qualities") or []) or "-",
    }


def file_row(f: dict) -> dict:
    """File-level metadata: every value comes from this one file."""
    return {
        "Season/Episode": _se(f), "Filename": f.get("file_name") or f["_id"],
        "Resolution": f.get("quality") or "-",
        "Audio": ", ".join(lang_name(c) for c in f.get("languages") or []) or "-",
        "Codec": " ".join(x for x in (f.get("video_codec"), f.get("audio_codec")) if x) or "-",
        "Size": human_size(f.get("size")), "Source message": f["_id"],
    }


def render_table(columns: tuple[str, ...], rows: list[dict]) -> str:
    cells = [[str(r.get(c, "")) for c in columns] for r in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    line = lambda vals: "  ".join(v.ljust(widths[i]) for i, v in enumerate(vals)).rstrip()  # noqa: E731
    return "\n".join([line(columns), line(["-" * w for w in widths]), *[line(r) for r in cells]])


async def files_of(db: Any, cid: str) -> list[dict]:
    cur = db[dbm.FILES].find({"collection_id": cid, "deleted": {"$ne": True}, "status": "identified"})
    out = [d async for d in cur]
    out.sort(key=lambda f: (f.get("season") if f.get("season") is not None else -1,
                            f.get("episode") if f.get("episode") is not None else -1,
                            quality_sort_key(f.get("quality")), f.get("file_name") or ""))
    return out


async def report(db: Any, cid: str | None = None, kind: str | None = None) -> dict:
    colls = [dbm.MOVIES, dbm.SERIES] if kind is None else [dbm.MOVIES if kind == "movie" else dbm.SERIES]
    titles: list[dict] = []
    for c in colls:
        flt = {"_id": cid} if cid else {}
        titles += [d async for d in db[c].find(flt, {"search_tokens": 0})]
    titles.sort(key=lambda t: ((t.get("title") or "").lower(), t.get("year") or 0))
    out = {"collections": [], "files": {}}
    for t in titles:
        out["collections"].append(collection_row(t))
        if cid:
            out["files"][t["_id"]] = [file_row(f) for f in await files_of(db, t["_id"])]
    return out


# ------------------------------------------------------------------------------ migration
async def migrate(db: Any, apply: bool = False) -> dict:
    stats = {"files_linked": 0, "titles_keyed": 0, "merged_groups": 0, "merged_files": 0, "duplicates_flagged": 0}
    cat = Catalog(db, _NoCache())

    # 1. file -> collection relationship
    flt = {"title_id": {"$ne": None}, "collection_id": {"$exists": False}}
    stats["files_linked"] = await db[dbm.FILES].count_documents(flt)
    if apply and stats["files_linked"]:
        async for f in db[dbm.FILES].find(flt, {"title_id": 1}):
            await db[dbm.FILES].update_one({"_id": f["_id"]}, {"$set": {"collection_id": f["title_id"]}})

    # 2. match_key on titles
    for coll in (dbm.MOVIES, dbm.SERIES):
        async for t in db[coll].find({"match_key": {"$exists": False}}, {"title": 1}):
            stats["titles_keyed"] += 1
            if apply:
                await db[coll].update_one({"_id": t["_id"]}, {"$set": {"match_key": match_key(t.get("title"))}})

    # 3. merge duplicate local collections
    for coll, kind in ((dbm.MOVIES, "movie"), (dbm.SERIES, "series")):
        groups: dict[str, list[dict]] = {}
        async for t in db[coll].find({"_id": {"$regex": "^loc_"}}, {"title": 1, "year": 1, "file_count": 1, "created_at": 1}):
            groups.setdefault(match_key(t.get("title")), []).append(t)
        for key, items in groups.items():
            if not key or len(items) < 2:
                continue
            for cluster in _compatible_clusters(items):
                if len(cluster) < 2:
                    continue
                cluster.sort(key=lambda t: (-(t.get("file_count") or 0), str(t.get("created_at") or ""), t["_id"]))
                keep, rest = cluster[0], cluster[1:]
                stats["merged_groups"] += 1
                for r in rest:
                    n = await db[dbm.FILES].count_documents({"title_id": r["_id"]})
                    stats["merged_files"] += n
                    if apply:
                        year = keep.get("year") or r.get("year")
                        if year and not keep.get("year"):
                            await db[coll].update_one({"_id": keep["_id"]}, {"$set": {"year": year}})
                        await cat.merge_titles(r["_id"], keep["_id"])
                        await db[dbm.FILES].update_many({"title_id": keep["_id"]}, {"$set": {"collection_id": keep["_id"]}})
    return stats


def _compatible_clusters(items: list[dict]) -> list[list[dict]]:
    """Group by year; year-less collections join a year only when exactly one known year exists."""
    dated = {}
    for t in items:
        if t.get("year"):
            dated.setdefault(t["year"], []).append(t)
    undated = [t for t in items if not t.get("year")]
    clusters = [list(v) for v in dated.values()]
    if undated:
        if len(clusters) == 1:
            clusters[0].extend(undated)
        else:
            clusters.append(undated)
    return clusters


class _NoCache:
    async def get(self, *a, **k):
        return None

    async def set(self, *a, **k):
        return None


async def _amain(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ajcine.tools.collections")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--id")
    r.add_argument("--kind", choices=["movie", "series"])
    r.add_argument("--json", action="store_true")
    m = sub.add_parser("migrate")
    m.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    from ..config import Settings
    s = Settings.from_env()
    db = await dbm.connect(s.mongo_uri, s.mongo_db)
    if a.cmd == "migrate":
        await dbm.ensure_indexes(db)
        st = await migrate(db, apply=a.apply)
        print(("APPLIED " if a.apply else "DRY RUN ") + json.dumps(st))
        return 0
    rep = await report(db, a.id, a.kind)
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    print(render_table(COLLECTION_COLUMNS, rep["collections"]))
    for cid, rows in rep["files"].items():
        print(f"\nFiles in {cid}")
        print(render_table(FILE_COLUMNS, rows))
    print("\nDeveloped by AJ APPLICATIONS · Developer: AJWEYA")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain(sys.argv[1:])))
