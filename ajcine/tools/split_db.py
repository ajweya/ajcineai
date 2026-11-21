
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from .. import db as dbm


async def split(source: Any, target: dbm.MultiDB, apply: bool = False) -> list[dict]:
    """Copy every collection into the database that owns it. Returns one report row per collection."""
    report = []
    for role, colls in dbm.ROLES.items():
        dest_db = target.dbs[role]
        same = dest_db is source or (getattr(dest_db, "name", None) == getattr(source, "name", 1)
                                     and getattr(dest_db, "client", 0) is getattr(source, "client", 2))
        for coll in colls:
            row = {"collection": coll, "role": role, "database": getattr(dest_db, "name", "?"),
                   "source": 0, "copied": 0, "skipped": same, "verified": None}
            if same:
                report.append(row)
                continue
            row["source"] = await source[coll].count_documents({})
            if apply and row["source"]:
                async for doc in source[coll].find({}):
                    await dest_db[coll].replace_one({"_id": doc["_id"]}, doc, upsert=True)
                    row["copied"] += 1
                row["verified"] = await dest_db[coll].count_documents({}) >= row["source"]
            report.append(row)
    if apply:
        await dbm.ensure_indexes(target)
    return report


async def _amain(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ajcine.tools.split_db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--source-uri")
    ap.add_argument("--source-db")
    a = ap.parse_args(argv)
    from ..config import Settings
    s = Settings.from_env()
    source = await dbm.connect(a.source_uri or s.mongo_uri, a.source_db or s.mongo_db)
    target = await dbm.connect_multi(s)
    rows = await split(source, target, apply=a.apply)
    print(("COPIED" if a.apply else "DRY RUN") + f" from database '{source.name}'")
    for r in rows:
        note = "same database, skipped" if r["skipped"] else f"{r['source']} docs"
        if a.apply and not r["skipped"]:
            note += f" -> copied {r['copied']}, verified={r['verified']}"
        print(f"  {r['collection']:<22} {r['role']:<9} -> {r['database']:<24} {note}")
    print("\nDeveloped by AJ APPLICATIONS · Developer: AJWEYA")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain(sys.argv[1:])))
