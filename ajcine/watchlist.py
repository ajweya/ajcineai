from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db as dbm

MAX_ITEMS = 100


class Watchlist:
    def __init__(self, db: Any):
        self.db = db

    async def toggle(self, user_id: int, title_id: str) -> bool | None:
        """Add or remove. Returns True when added, False when removed, None when the list is full."""
        coll = self.db[dbm.WATCHLIST]
        if await coll.find_one({"user_id": user_id, "title_id": title_id}):
            await coll.delete_one({"user_id": user_id, "title_id": title_id})
            return False
        if await coll.count_documents({"user_id": user_id}) >= MAX_ITEMS:
            return None
        await coll.insert_one({"user_id": user_id, "title_id": title_id, "added_at": datetime.now(timezone.utc)})
        return True

    async def has(self, user_id: int, title_id: str) -> bool:
        return bool(await self.db[dbm.WATCHLIST].find_one({"user_id": user_id, "title_id": title_id}))

    async def titles(self, user_id: int, catalog: Any, limit: int = 30) -> list[dict]:
        out = []
        cur = self.db[dbm.WATCHLIST].find({"user_id": user_id}).sort("added_at", -1).limit(limit)
        async for w in cur:
            t = await catalog.get_title(w["title_id"])
            if t and (t.get("file_count") or 0) > 0:
                out.append(t)
        return out
