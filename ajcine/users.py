"""Users, bans, permissions and administrator roles."""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any

from . import db as dbm

ROLES = ("viewer", "moderator", "admin", "owner")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}

# permission -> minimum role
PERMISSIONS = {
    "view": "viewer",
    "review": "moderator",
    "media_edit": "moderator",
    "indexing": "admin",
    "sources": "admin",
    "ai": "admin",
    "tmdb": "admin",
    "users": "admin",
    "settings": "admin",
    "broadcast": "admin",
    "ads": "admin",
    "cache": "admin",
    "backups": "owner",
    "admins": "owner",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def role_allows(role: str | None, permission: str) -> bool:
    need = PERMISSIONS.get(permission)
    if need is None or role not in ROLE_RANK:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[need]


# ---- password hashing (stdlib scrypt; no extra dependency) ----------------------------------
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, TypeError):
        return False


class UserService:
    def __init__(self, db: Any, owner_ids: tuple[int, ...] = ()):
        self.db = db
        self.owner_ids = set(owner_ids)

    # ---- bot users ----------------------------------------------------------------------------
    async def touch(self, tg_user: Any) -> dict:
        uid = tg_user.id
        base = {"first_name": getattr(tg_user, "first_name", None), "username": getattr(tg_user, "username", None),
                "last_seen": now(), "blocked": False}
        await self.db[dbm.USERS].update_one(
            {"_id": uid}, {"$set": base, "$setOnInsert": {
                "joined_at": now(), "banned": False, "permissions": {"search": True, "download": True},
                "search_count": 0, "delivery_count": 0}}, upsert=True)
        return await self.db[dbm.USERS].find_one({"_id": uid})

    async def get(self, uid: int) -> dict | None:
        return await self.db[dbm.USERS].find_one({"_id": uid})

    async def is_banned(self, uid: int) -> bool:
        if uid in self.owner_ids:
            return False
        d = await self.db[dbm.USERS].find_one({"_id": uid}, {"banned": 1})
        return bool(d and d.get("banned"))

    async def can(self, uid: int, permission: str) -> bool:
        if uid in self.owner_ids:
            return True
        d = await self.db[dbm.USERS].find_one({"_id": uid}, {"banned": 1, "permissions": 1})
        if d and d.get("banned"):
            return False
        perms = (d or {}).get("permissions") or {}
        return bool(perms.get(permission, True))

    async def ban(self, uid: int, reason: str | None = None) -> bool:
        if uid in self.owner_ids:
            return False
        await self.db[dbm.USERS].update_one(
            {"_id": uid}, {"$set": {"banned": True, "ban_reason": (reason or "")[:200], "banned_at": now()},
                           "$setOnInsert": {"joined_at": now(), "permissions": {"search": True, "download": True}}}, upsert=True)
        return True

    async def unban(self, uid: int) -> bool:
        r = await self.db[dbm.USERS].update_one({"_id": uid}, {"$set": {"banned": False, "ban_reason": None}})
        return bool(r.matched_count)

    async def set_permission(self, uid: int, permission: str, allowed: bool) -> bool:
        if permission not in ("search", "download"):
            raise ValueError("unknown permission")
        r = await self.db[dbm.USERS].update_one({"_id": uid}, {"$set": {f"permissions.{permission}": bool(allowed)}})
        return bool(r.matched_count)

    async def count_activity(self, uid: int, field: str) -> None:
        if field in ("search_count", "delivery_count"):
            await self.db[dbm.USERS].update_one({"_id": uid}, {"$inc": {field: 1}})

    async def list(self, page: int = 1, per_page: int = 25, q: str | None = None, banned: bool | None = None) -> dict:
        f: dict = {}
        if q:
            if q.lstrip("-").isdigit():
                f["_id"] = int(q)
            else:
                f["username"] = {"$regex": q.lstrip("@"), "$options": "i"}
        if banned is not None:
            f["banned"] = banned
        total = await self.db[dbm.USERS].count_documents(f)
        cur = self.db[dbm.USERS].find(f).sort("last_seen", -1).skip(max(0, page - 1) * per_page).limit(per_page)
        return {"total": total, "page": page, "per_page": per_page,
                "items": [{**d, "id": d.pop("_id")} async for d in cur]}

    async def activity(self, uid: int, limit: int = 30) -> dict:
        s = [d async for d in self.db[dbm.SEARCH_HISTORY].find({"user_id": uid}, {"_id": 0}).sort("ts", -1).limit(limit)]
        dl = [d async for d in self.db[dbm.DELIVERIES].find({"user_id": uid}, {"_id": 0}).sort("ts", -1).limit(limit)]
        return {"searches": s, "deliveries": dl}

    # ---- telegram administrators (bot panel) -----------------------------------------------------------
    async def telegram_role(self, uid: int) -> str | None:
        if uid in self.owner_ids:
            return "owner"
        d = await self.db[dbm.ADMINS].find_one({"telegram_id": uid, "active": {"$ne": False}})
        return d["role"] if d and d.get("role") in ROLE_RANK else None

    async def grant_telegram_admin(self, uid: int, role: str, by: int | None = None) -> None:
        if role not in ROLE_RANK or role == "owner":
            raise ValueError("role must be viewer, moderator or admin")
        await self.db[dbm.ADMINS].update_one(
            {"telegram_id": uid, "username": None}, {"$set": {"role": role, "active": True, "granted_by": by, "updated_at": now()},
                                                     "$setOnInsert": {"created_at": now()}}, upsert=True)

    async def revoke_telegram_admin(self, uid: int) -> bool:
        r = await self.db[dbm.ADMINS].update_many({"telegram_id": uid}, {"$set": {"active": False}})
        return bool(r.matched_count)
