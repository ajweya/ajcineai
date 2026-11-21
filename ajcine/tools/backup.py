"""Backup and restore of the MongoDB catalog (gzip'd extended-JSON).

CLI:
    python -m ajcine.tools.backup create            # write data/backups/ajcine-<ts>.json.gz
    python -m ajcine.tools.backup restore FILE      # restore (replaces listed collections)

Excluded on purpose: web sessions, delivery tokens and caches (ephemeral/secret).
Admin password hashes ARE included: treat backup files as secrets.
"""
from __future__ import annotations

import asyncio
import gzip
import os
import sys
import time
from typing import Any

from bson import json_util

from .. import db as dbm

BACKUP_COLLECTIONS = [
    dbm.USERS, dbm.ADMINS, dbm.SOURCES, dbm.MOVIES, dbm.SERIES, dbm.SEASONS, dbm.EPISODES, dbm.FILES,
    dbm.LANGUAGES, dbm.QUEUE, dbm.CORRECTIONS, dbm.TITLE_DECISIONS, dbm.SEARCH_HISTORY, dbm.DELIVERIES,
    dbm.JOBS, dbm.SETTINGS, dbm.REQUESTS,
]


async def create_backup(db: Any, directory: str) -> dict:
    os.makedirs(directory, exist_ok=True)
    name = f"ajcine-{time.strftime('%Y%m%d-%H%M%S')}.json.gz"
    final = os.path.join(directory, name)
    tmp = final + ".part"
    counts: dict[str, int] = {}
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for coll in BACKUP_COLLECTIONS:
            n = 0
            async for doc in db[coll].find({}):
                fh.write(json_util.dumps({"c": coll, "d": doc}) + "\n")
                n += 1
            counts[coll] = n
    os.replace(tmp, final)
    try:
        os.chmod(final, 0o600)
    except OSError:
        pass
    return {"file": name, "path": final, "bytes": os.path.getsize(final), "counts": counts}


def list_backups(directory: str) -> list[dict]:
    if not os.path.isdir(directory):
        return []
    out = []
    for n in sorted(os.listdir(directory), reverse=True):
        if n.endswith(".json.gz"):
            p = os.path.join(directory, n)
            out.append({"file": n, "bytes": os.path.getsize(p), "modified": os.path.getmtime(p)})
    return out


async def restore_backup(db: Any, path: str, wipe: bool = True) -> dict:
    counts: dict[str, int] = {}
    seen: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        batch: dict[str, list] = {}
        for line in fh:
            rec = json_util.loads(line)
            coll = rec["c"]
            if coll not in BACKUP_COLLECTIONS:
                raise ValueError(f"unexpected collection in backup: {coll}")
            if coll not in seen:
                seen.add(coll)
                if wipe:
                    await db[coll].delete_many({})
            batch.setdefault(coll, []).append(rec["d"])
            if len(batch[coll]) >= 500:
                await db[coll].insert_many(batch[coll])
                counts[coll] = counts.get(coll, 0) + len(batch[coll])
                batch[coll] = []
        for coll, docs in batch.items():
            if docs:
                await db[coll].insert_many(docs)
                counts[coll] = counts.get(coll, 0) + len(docs)
    return counts


async def _main(argv: list[str]) -> int:
    from ..config import Settings

    s = Settings.from_env()
    db = await dbm.connect(s.mongo_uri, s.mongo_db)
    if len(argv) >= 1 and argv[0] == "create":
        r = await create_backup(db, s.backup_dir)
        print(f"backup written: {r['path']} ({r['bytes']} bytes)")
        return 0
    if len(argv) == 2 and argv[0] == "restore":
        print("restoring (existing data in backed-up collections is replaced)...")
        counts = await restore_backup(db, argv[1])
        await dbm.ensure_indexes(db)
        print("restored:", counts)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
