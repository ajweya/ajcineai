
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from . import db as dbm
from .parsing.text import match_key

STATUSES = ("open", "done", "declined")


def now() -> datetime:
    return datetime.now(timezone.utc)


class RequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class RequestService:
    def __init__(self, db: Any, cache: Any, search: Any):
        self.db, self.cache, self.search = db, cache, search

    @staticmethod
    def _voter(secret: str, who: str) -> str:
        return hashlib.sha256(f"{secret}:{who}".encode()).hexdigest()[:24]

    async def submit(self, title: str, year: int | None, kind: str | None, who: str, secret: str = "ajcine") -> dict:
        """`who` is an opaque id of the requester (IP or Telegram id); only a salted hash of it is stored."""
        title = " ".join((title or "").split())[:120]
        if len(match_key(title)) < 2:
            raise RequestError("invalid", "Enter the name of the movie or series.")
        if kind not in (None, "movie", "series"):
            raise RequestError("invalid", "Type must be movie or series.")
        if year is not None and not 1888 <= year <= 2100:
            raise RequestError("invalid", "Enter a valid year.")
        if await self.cache.incr(f"rq:{who}", 3600) > 5:
            raise RequestError("rate", "You have sent several requests already. Please try again later.")
        # Already in the library? Say so instead of creating a request.
        res = await self.search.search(title, kind=kind, year=year, record=False, use_ai=False)
        if res["items"] and res["items"][0]["score"] >= 0.86 and res["items"][0]["file_count"] > 0:
            top = res["items"][0]
            return {"status": "exists", "title_id": top["id"], "title": top["title"]}
        key = f"{kind or 'any'}:{match_key(title)}:{year or ''}"
        voter = self._voter(secret, who)
        doc = await self.db[dbm.REQUESTS].find_one({"key": key})
        if doc:
            if voter in doc.get("voters", []):
                return {"status": "already", "votes": doc["votes"], "request_status": doc["status"]}
            await self.db[dbm.REQUESTS].update_one({"_id": doc["_id"]}, {"$addToSet": {"voters": voter},
                                                                          "$inc": {"votes": 1}, "$set": {"updated_at": now()}})
            return {"status": "voted", "votes": doc["votes"] + 1, "request_status": doc["status"]}
        await self.db[dbm.REQUESTS].insert_one({
            "key": key, "title": title, "year": year, "kind": kind, "status": "open", "votes": 1, "voters": [voter],
            "created_at": now(), "updated_at": now()})
        return {"status": "created", "votes": 1, "request_status": "open"}

    async def listing(self, status: str = "open", limit: int = 100) -> list[dict]:
        out = []
        cur = self.db[dbm.REQUESTS].find({"status": status}, {"voters": 0}).sort([("votes", -1), ("created_at", 1)]).limit(limit)
        async for d in cur:
            d["_id"] = str(d["_id"])
            match = None
            if status == "open":      # the title may have been indexed since it was requested
                res = await self.search.search(d["title"], kind=d.get("kind"), year=d.get("year"), record=False, use_ai=False)
                if res["items"] and res["items"][0]["score"] >= 0.86 and res["items"][0]["file_count"] > 0:
                    match = {"id": res["items"][0]["id"], "title": res["items"][0]["title"]}
            d["in_library"] = match
            out.append(d)
        return out

    async def set_status(self, request_id: str, status: str) -> bool:
        from bson import ObjectId
        if status not in STATUSES:
            raise RequestError("invalid", "Unknown status.")
        try:
            oid = ObjectId(request_id)
        except Exception:
            raise RequestError("invalid", "Unknown request.")
        r = await self.db[dbm.REQUESTS].update_one({"_id": oid}, {"$set": {"status": status, "updated_at": now()}})
        return bool(r.matched_count)

    async def delete(self, request_id: str) -> bool:
        from bson import ObjectId
        try:
            oid = ObjectId(request_id)
        except Exception:
            return False
        return bool((await self.db[dbm.REQUESTS].delete_one({"_id": oid})).deleted_count)
