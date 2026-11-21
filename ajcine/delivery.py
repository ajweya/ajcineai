"""Private file delivery with secure, user-bound, expiring tokens.

Flow: the user taps a quality button -> a token is created for THAT user ->
Telegram opens the bot's private chat via t.me/<bot>?start=dl_<token> -> the
bot validates the token (exists, not expired, same user, not over its use
limit, user not banned/restricted) -> the actual indexed message is copied to
the user. No fake URLs; the bytes come from Telegram.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db as dbm
from .catalog import Catalog, human_size
from .config import Settings
from .brand import PRODUCT

try:
    from pyrogram.errors import FloodWait
except Exception:  # pragma: no cover
    class FloodWait(Exception):  # type: ignore
        value = 5

log = logging.getLogger("ajcine.delivery")

TOKEN_PREFIX = "dl_"
_GONE = {"MessageIdInvalid", "MessageEmpty", "MessageNotModified", "ChannelPrivate", "ChannelInvalid",
         "ChatForbidden", "PeerIdInvalid", "MessageDeleteForbidden"}


class DeliveryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def now() -> datetime:
    return datetime.now(timezone.utc)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeliveryService:
    def __init__(self, settings: Settings, db: Any, cache: Any, catalog: Catalog, users: Any):
        self.s = settings
        self.db = db
        self.cache = cache
        self.catalog = catalog
        self.users = users

    # ---- tokens ------------------------------------------------------------------------------------
    async def create_token(self, user_id: int, file_ids: list[str], title_id: str | None = None) -> str:
        if not file_ids:
            raise DeliveryError("empty", "nothing to deliver")
        if await self.cache.incr(f"mk:{user_id}", 60) > self.s.rate_limit_per_minute:
            raise DeliveryError("rate", "Too many requests. Please wait a minute.")
        token = secrets.token_urlsafe(16)
        await self.db[dbm.DELIVERY_TOKENS].insert_one({
            "_id": _h(token), "user_id": user_id, "file_ids": file_ids[:30], "title_id": title_id,
            "created_at": now(), "expires_at": now() + timedelta(seconds=self.s.token_ttl_seconds),
            "uses": 0, "max_uses": self.s.token_max_uses, "kind": "dl",
        })
        return token

    async def create_web_token(self, file_ids: list[str], title_id: str | None, ip: str) -> str:
        """Download token requested from the website. It starts unbound and is claimed by the first Telegram
        account that opens its deep link; from then on it behaves like any user-bound token."""
        if not file_ids:
            raise DeliveryError("empty", "nothing to deliver")
        if await self.cache.incr(f"wk:{ip}", 60) > self.s.rate_limit_per_minute:
            raise DeliveryError("rate", "Too many requests. Please wait a minute.")
        token = secrets.token_urlsafe(16)
        await self.db[dbm.DELIVERY_TOKENS].insert_one({
            "_id": _h(token), "user_id": None, "file_ids": file_ids[:30], "title_id": title_id,
            "created_at": now(), "expires_at": now() + timedelta(seconds=self.s.token_ttl_seconds),
            "uses": 0, "max_uses": self.s.token_max_uses, "kind": "dl", "origin": "web",
        })
        return token

    async def create_stream_token(self, file_id: str, title_id: str | None) -> str:
        """Opaque, expiring token for in-browser playback. Only a hash is stored; no Telegram ids leave the server."""
        token = secrets.token_urlsafe(18)
        await self.db[dbm.DELIVERY_TOKENS].insert_one({
            "_id": _h(token), "kind": "stream", "file_id": file_id, "title_id": title_id,
            "created_at": now(), "expires_at": now() + timedelta(seconds=self.s.stream_token_ttl_seconds),
        })
        return token

    async def resolve_stream(self, token: str) -> dict:
        if not token or len(token) > 64 or not all(c.isalnum() or c in "-_" for c in token):
            raise DeliveryError("invalid", "This link is not valid.")
        doc = await self.db[dbm.DELIVERY_TOKENS].find_one({"_id": _h(token), "kind": "stream"})
        if not doc:
            raise DeliveryError("invalid", "This link is not valid or has expired.")
        exp = doc["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now():
            raise DeliveryError("expired", "This link has expired.")
        return doc

    async def resolve_token(self, token: str, user_id: int) -> dict:
        if not token or len(token) > 64 or not all(c.isalnum() or c in "-_" for c in token):
            raise DeliveryError("invalid", "This link is not valid.")
        if await self.cache.incr(f"rt:{user_id}", 60) > self.s.rate_limit_per_minute * 2:
            raise DeliveryError("rate", "Too many requests. Please wait a minute.")
        doc = await self.db[dbm.DELIVERY_TOKENS].find_one({"_id": _h(token), "kind": {"$ne": "stream"}})
        if not doc:
            raise DeliveryError("invalid", "This link is not valid or has expired. Please search again.")
        if doc.get("user_id") is None:  # website token: the first Telegram account to open it owns it
            claimed = await self.db[dbm.DELIVERY_TOKENS].find_one_and_update(
                {"_id": doc["_id"], "user_id": None}, {"$set": {"user_id": user_id, "claimed_at": now()}})
            doc = await self.db[dbm.DELIVERY_TOKENS].find_one({"_id": doc["_id"]})
            if claimed is None and doc and doc.get("user_id") != user_id:
                raise DeliveryError("forbidden", "This link was created for another user.")
        exp = doc["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now():
            raise DeliveryError("expired", "This link has expired. Please search again.")
        if doc["user_id"] != user_id:
            log.warning("token used by a different user (owner=%s, caller=%s)", doc["user_id"], user_id)
            raise DeliveryError("forbidden", "This link was created for another user.")
        if doc["uses"] >= doc["max_uses"]:
            raise DeliveryError("used", "This link has been used too many times. Please search again.")
        if await self.users.is_banned(user_id):
            raise DeliveryError("banned", "Your access has been disabled.")
        if not await self.users.can(user_id, "download"):
            raise DeliveryError("restricted", "Downloads are not enabled for your account.")
        return doc

    # ---- subscription gate -----------------------------------------------------------------------------
    async def missing_subscriptions(self, client: Any, user_id: int) -> list[str]:
        missing = []
        for ch in self.s.force_sub_channels:
            ref = int(ch) if ch.lstrip("-").isdigit() else ch
            try:
                m = await client.get_chat_member(ref, user_id)
                if str(getattr(m, "status", "")).split(".")[-1].lower() in ("left", "banned", "restricted") and \
                        not getattr(m, "is_member", True):
                    missing.append(ch)
            except Exception as exc:
                if type(exc).__name__ == "UserNotParticipant":
                    missing.append(ch)
                else:
                    log.error("force-sub check failed for %s: %s", ch, type(exc).__name__)
        return missing

    # ---- sending ------------------------------------------------------------------------------------------
    @staticmethod
    def caption_for(f: dict, title: dict | None) -> str:
        name = html.escape((title or {}).get("title") or f.get("file_name") or "File")
        year = (title or {}).get("year")
        head = f"🎬 <b>{name}</b>" + (f" ({year})" if year else "")
        lines = [head]
        if f.get("season") is not None and f.get("episode") is not None:
            ep = f"S{f['season']:02d}E{f['episode']:02d}"
            if f.get("episode_title"):
                ep += f" · {html.escape(f['episode_title'])}"
            lines.append(ep)
        elif f.get("is_season_pack") and f.get("season") is not None:
            lines.append(f"Season {f['season']} · Complete pack")
        elif f.get("is_complete_series"):
            lines.append("Complete series pack")
        bits = []
        from .parsing.languages import lang_name
        if f.get("languages"):
            bits.append("🗣 " + ", ".join(lang_name(c) for c in f["languages"]))
        if f.get("quality"):
            bits.append("🎞 " + str(f["quality"]))
        if f.get("size"):
            bits.append("💾 " + human_size(f["size"]))
        if bits:
            lines.append(" · ".join(bits))
        lines.append(f"\n<i>{PRODUCT}</i>")
        return "\n".join(lines)[:1000]

    async def _send_one(self, client: Any, user_id: int, f: dict, caption: str) -> Any:
        sources = [(f["source_chat_id"], f["source_message_id"])]
        sources += [(a["chat_id"], a["message_id"]) for a in f.get("alt_sources", [])]
        gone = 0
        for chat_id, mid in sources:
            for attempt in range(2):
                try:
                    return await client.copy_message(user_id, chat_id, mid, caption=caption,
                                                     protect_content=self.s.protect_content)
                except FloodWait as fw:
                    await asyncio.sleep(int(getattr(fw, "value", 3)) + 1)
                    continue
                except Exception as exc:
                    if type(exc).__name__ in _GONE:
                        gone += 1
                        break
                    raise DeliveryError("telegram", f"Telegram error: {type(exc).__name__}")
        if f.get("file_id"):  # last resort: cached file id (valid if indexed by this bot)
            try:
                return await client.send_cached_media(user_id, f["file_id"], caption=caption,
                                                      protect_content=self.s.protect_content)
            except Exception:
                pass
        raise DeliveryError("missing", "This file is no longer available in the source channel.")

    async def deliver(self, client: Any, token: str, user_id: int, notify: Any | None = None) -> dict:
        """Validate the token and send every file. Returns a report dict."""
        doc = await self.resolve_token(token, user_id)
        await self.db[dbm.DELIVERY_TOKENS].update_one({"_id": doc["_id"]}, {"$inc": {"uses": 1}})
        sent, missing = 0, 0
        title = await self.catalog.get_title(doc["title_id"]) if doc.get("title_id") else None
        messages = []
        for fid in doc["file_ids"]:
            f = await self.db[dbm.FILES].find_one({"_id": fid, "deleted": {"$ne": True}})
            if not f or f.get("status") != "identified":
                missing += 1
                continue
            try:
                msg = await self._send_one(client, user_id, f, self.caption_for(f, title))
                sent += 1
                messages.append(msg)
                await self._log(user_id, f, "delivered")
            except DeliveryError as exc:
                if exc.code == "missing":
                    missing += 1
                    await self._mark_missing(f)
                    await self._log(user_id, f, "missing")
                else:
                    await self._log(user_id, f, "error")
                    raise
        if sent:
            await self.users.count_activity(user_id, "delivery_count")
            if self.s.delivery_autodelete_minutes > 0:
                asyncio.create_task(self._autodelete(client, user_id, messages, self.s.delivery_autodelete_minutes * 60))
        return {"sent": sent, "missing": missing, "autodelete_minutes": self.s.delivery_autodelete_minutes if sent else 0}

    async def mark_missing(self, f: dict) -> None:
        await self._mark_missing(f)

    async def _mark_missing(self, f: dict) -> None:
        await self.db[dbm.FILES].update_one({"_id": f["_id"]}, {"$set": {"missing": True, "missing_at": now()}})
        if f.get("title_id"):
            await self.catalog.refresh_title_summary(f["title_id"])
        log.warning("source message missing for file %s", f["_id"])

    async def _log(self, user_id: int, f: dict, status: str) -> None:
        try:
            await self.db[dbm.DELIVERIES].insert_one({
                "user_id": user_id, "file_id": f["_id"], "title_id": f.get("title_id"), "status": status,
                "season": f.get("season"), "episode": f.get("episode"), "quality": f.get("quality"),
                "size": f.get("size"), "ts": now()})
        except Exception:
            pass

    @staticmethod
    async def _autodelete(client: Any, user_id: int, messages: list, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            await client.delete_messages(user_id, [m.id for m in messages if m is not None])
        except Exception:
            pass
