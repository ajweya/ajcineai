"""Broadcasting to bot users and groups, plus the registry of groups the bot is in.

Recipients are bot users (who started the bot, not banned, not blocked) and groups/supergroups where the bot is a
member. Sending is paced (BROADCAST_RATE_PER_SECOND), honours flood waits, marks users who blocked the bot and
groups that removed it, can be cancelled, and resumes after a restart from a stored cursor (a restart can repeat the
last few messages). A test message can be sent to an administrator first.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from . import db as dbm

log = logging.getLogger("ajcine.broadcast")
URL_RE = re.compile(r"^https://[^\s]{4,500}$")
AUDIENCES = ("users", "groups", "all")
UNREACHABLE = {"UserIsBlocked", "InputUserDeactivated", "UserDeactivated", "UserDeactivatedBan", "PeerIdInvalid",
               "ChatWriteForbidden", "ChannelPrivate", "ChatAdminRequired", "ChatForbidden", "UserBannedInChannel",
               "ChannelInvalid", "ChatIdInvalid"}
GROUP_TYPES = ("GROUP", "SUPERGROUP")
CHECKPOINT = 25


def now() -> datetime:
    return datetime.now(timezone.utc)


class BroadcastError(Exception):
    pass


def clean_message(data: dict) -> dict:
    text = str(data.get("text") or "").strip()
    image = str(data.get("image_url") or "").strip()
    btn_text = " ".join(str(data.get("button_text") or "").split())[:24]
    btn_url = str(data.get("button_url") or "").strip()
    audience = data.get("audience") or "users"
    if audience not in AUDIENCES:
        raise BroadcastError("Audience must be users, groups or all.")
    if not text:
        raise BroadcastError("Write the message text.")
    if len(text) > (1000 if image else 3900):
        raise BroadcastError("The text is too long" + (" for a message with an image (limit 1000)." if image else "."))
    if image and not URL_RE.match(image):
        raise BroadcastError("The image must be a direct https:// address.")
    if bool(btn_url) != bool(btn_text):
        raise BroadcastError("Give the button both a label and a link, or neither.")
    if btn_url and not URL_RE.match(btn_url):
        raise BroadcastError("The button link must be a full https:// address.")
    return {"text": text, "image_url": image or None, "button_text": btn_text or None, "button_url": btn_url or None,
            "audience": audience}


class ChatRegistry:
    """Groups and supergroups the bot is a member of (needed to broadcast to groups)."""

    def __init__(self, db: Any, cache: Any):
        self.db, self.cache = db, cache

    async def track(self, chat: Any, force: bool = False) -> None:
        kind = getattr(getattr(chat, "type", None), "name", "")
        if kind not in GROUP_TYPES:
            return
        if await self.cache.incr(f"chat:{chat.id}", 3600) > 1 and not force:   # one write per hour per group
            return
        await self.db[dbm.BOT_CHATS].update_one(
            {"_id": chat.id}, {"$set": {"type": kind, "title": (getattr(chat, "title", "") or "")[:120],
                                        "username": getattr(chat, "username", None), "active": True, "last_seen": now()},
                               "$setOnInsert": {"joined_at": now()}}, upsert=True)

    async def left(self, chat_id: int) -> None:
        await self.db[dbm.BOT_CHATS].update_one({"_id": chat_id}, {"$set": {"active": False, "left_at": now()}})

    async def counts(self) -> dict:
        users = await self.db[dbm.USERS].count_documents({"banned": {"$ne": True}, "blocked": {"$ne": True}})
        groups = await self.db[dbm.BOT_CHATS].count_documents({"active": True})
        return {"users": users, "groups": groups}

    async def listing(self, limit: int = 200) -> list[dict]:
        return [d async for d in self.db[dbm.BOT_CHATS].find({}).sort("last_seen", -1).limit(limit)]


class BroadcastService:
    def __init__(self, app: Any):
        self.app = app
        self.tasks: dict[str, asyncio.Task] = {}

    @property
    def db(self):
        return self.app.db

    # ---- management -------------------------------------------------------------------------------
    async def create(self, data: dict, actor: str) -> dict:
        doc = {**clean_message(data), "status": "draft", "created_by": actor, "created_at": now(),
               "cursor": {"users": None, "groups": None}, "stats": {"sent": 0, "failed": 0, "unreachable": 0},
               "total": None}
        r = await self.db[dbm.BROADCASTS].insert_one(doc)
        doc["_id"] = str(r.inserted_id)
        return doc

    async def get(self, bid: str) -> dict | None:
        d = await self.db[dbm.BROADCASTS].find_one({"_id": _oid(bid)})
        if d:
            d["_id"] = str(d["_id"])
        return d

    async def listing(self, limit: int = 30) -> list[dict]:
        out = []
        async for d in self.db[dbm.BROADCASTS].find({}).sort("created_at", -1).limit(limit):
            d["_id"] = str(d["_id"])
            out.append(d)
        return out

    async def delete(self, bid: str) -> bool:
        d = await self.get(bid)
        if not d or d["status"] in ("queued", "running"):
            return False
        return bool((await self.db[dbm.BROADCASTS].delete_one({"_id": _oid(bid)})).deleted_count)

    async def start(self, bid: str) -> dict:
        d = await self.get(bid)
        if not d:
            raise BroadcastError("Broadcast not found.")
        if d["status"] != "draft":
            raise BroadcastError("Only a draft can be started.")
        counts = await self.app.chats.counts()
        total = (counts["users"] if d["audience"] in ("users", "all") else 0) + \
                (counts["groups"] if d["audience"] in ("groups", "all") else 0)
        if total == 0:
            raise BroadcastError("There is nobody to send this to yet.")
        await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid)}, {"$set": {"status": "queued", "total": total,
                                                                               "started_at": now()}})
        self._spawn(bid)
        return await self.get(bid)

    async def cancel(self, bid: str) -> bool:
        r = await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid), "status": {"$in": ["queued", "running"]}},
                                                     {"$set": {"status": "cancelled", "finished_at": now()}})
        return bool(r.matched_count)

    async def resume_all(self) -> int:
        n = 0
        async for d in self.db[dbm.BROADCASTS].find({"status": {"$in": ["queued", "running"]}}, {"_id": 1}):
            self._spawn(str(d["_id"]))
            n += 1
        return n

    def _spawn(self, bid: str) -> None:
        t = self.tasks.get(bid)
        if t and not t.done():
            return
        self.tasks[bid] = asyncio.create_task(self.run(bid), name=f"broadcast-{bid}")

    # ---- sending ----------------------------------------------------------------------------------
    def _keyboard(self, d: dict):
        if not d.get("button_url"):
            return None
        from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([[InlineKeyboardButton(d["button_text"], url=d["button_url"])]])

    async def send_one(self, client: Any, chat_id: int, d: dict) -> None:
        text = html.escape(d["text"], quote=False)
        kb = self._keyboard(d)
        if d.get("image_url"):
            await client.send_photo(chat_id, d["image_url"], caption=text, reply_markup=kb)
        else:
            await client.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)

    async def test(self, bid: str, chat_id: int) -> None:
        d = await self.get(bid)
        if not d:
            raise BroadcastError("Broadcast not found.")
        try:
            await self.send_one(self.app.tg, chat_id, d)
        except Exception as exc:
            raise BroadcastError(f"The test message could not be sent ({type(exc).__name__}). "
                                 "Open the bot and press Start first.") from exc

    async def _recipients(self, kind: str, after: Any):
        """Stable, resumable iteration: ids in ascending order after the stored cursor."""
        if kind == "users":
            coll, flt = self.db[dbm.USERS], {"banned": {"$ne": True}, "blocked": {"$ne": True}}
        else:
            coll, flt = self.db[dbm.BOT_CHATS], {"active": True}
        if after is not None:
            flt = {**flt, "_id": {"$gt": after}}
        async for r in coll.find(flt, {"_id": 1}).sort("_id", 1):
            yield r["_id"]

    async def _mark_unreachable(self, kind: str, rid: int) -> None:
        if kind == "users":
            await self.db[dbm.USERS].update_one({"_id": rid}, {"$set": {"blocked": True}})
        else:
            await self.db[dbm.BOT_CHATS].update_one({"_id": rid}, {"$set": {"active": False, "left_at": now()}})

    async def run(self, bid: str) -> None:
        d = await self.get(bid)
        if not d or d["status"] not in ("queued", "running"):
            return
        await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid)}, {"$set": {"status": "running"}})
        stats = dict(d["stats"])
        cursor = dict(d["cursor"])
        delay = 1.0 / self.app.settings.broadcast_rate_per_second
        kinds = [k for k, on in (("users", d["audience"] in ("users", "all")), ("groups", d["audience"] in ("groups", "all"))) if on]
        pending = 0
        try:
            for kind in kinds:
                async for rid in self._recipients(kind, cursor.get(kind)):
                    if pending % CHECKPOINT == 0:
                        cur = await self.db[dbm.BROADCASTS].find_one({"_id": _oid(bid)}, {"status": 1})
                        if not cur or cur["status"] != "running":
                            return                                  # cancelled
                    outcome = await self._deliver(rid, d)
                    stats[outcome] += 1
                    if outcome == "unreachable":
                        await self._mark_unreachable(kind, rid)
                    cursor[kind] = rid
                    pending += 1
                    if pending % CHECKPOINT == 0:
                        await self._save(bid, stats, cursor)
                    await asyncio.sleep(delay)
            await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid), "status": "running"}, {"$set": {
                "status": "done", "finished_at": now(), "stats": stats, "cursor": cursor}})
        except asyncio.CancelledError:
            await self._save(bid, stats, cursor)
            raise
        except Exception as exc:
            log.exception("broadcast %s failed", bid)
            await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid)}, {"$set": {
                "status": "failed", "error": type(exc).__name__, "stats": stats, "cursor": cursor, "finished_at": now()}})

    async def _save(self, bid: str, stats: dict, cursor: dict) -> None:
        await self.db[dbm.BROADCASTS].update_one({"_id": _oid(bid), "status": "running"},
                                                 {"$set": {"stats": stats, "cursor": cursor}})

    async def _deliver(self, chat_id: int, d: dict) -> str:
        for attempt in range(2):
            try:
                await self.send_one(self.app.tg, chat_id, d)
                return "sent"
            except Exception as exc:
                name = type(exc).__name__
                if name == "FloodWait" and attempt == 0:
                    await asyncio.sleep(min(300, int(getattr(exc, "value", 5) or 5) + 1))
                    continue
                if name in UNREACHABLE:
                    return "unreachable"
                log.warning("broadcast send failed: %s", name)
                return "failed"
        return "failed"


def _oid(v: str) -> ObjectId:
    try:
        return ObjectId(v)
    except Exception:
        return ObjectId("0" * 24)
