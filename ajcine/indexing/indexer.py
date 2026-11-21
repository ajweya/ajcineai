
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .. import db as dbm
from ..config import Settings
from ..identify.pipeline import IdentificationPipeline
from ..parsing.parser import ARCHIVE_EXTS, AUDIO_EXTS, VIDEO_EXTS

try: 
    from pyrogram.errors import FloodWait
except Exception:  # pragma: no cover
    class FloodWait(Exception):  # type: ignore
        value = 5

log = logging.getLogger("ajcine.indexer")


def now() -> datetime:
    return datetime.now(timezone.utc)


class SourceAccessError(RuntimeError):
    """The source cannot be read (not a member/admin, private, deleted)."""


def extract_media(msg: Any, allow_archives: bool = False) -> dict | None:
    """Return normalized media metadata for a Pyrogram message, or None."""
    if msg is None or getattr(msg, "empty", False) or getattr(msg, "service", None):
        return None
    media, kind = None, None
    for attr in ("video", "document", "audio"):
        m = getattr(msg, attr, None)
        if m is not None:
            media, kind = m, attr
            break
    if media is None:
        return None
    name = getattr(media, "file_name", None)
    mime = getattr(media, "mime_type", None) or ""
    caption = (getattr(msg, "caption", None) or "").strip()
    ext = os.path.splitext(name)[1].lstrip(".").lower() if name else ""
    if kind == "document":
        ok = mime.startswith("video/") or ext in VIDEO_EXTS or (allow_archives and ext in ARCHIVE_EXTS)
        if not ok and mime.startswith("audio/"):
            ok = True
        if not ok:
            return None
    elif kind == "audio" and ext and ext not in AUDIO_EXTS and not mime.startswith("audio/"):
        return None
    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None)
    mid = getattr(msg, "id", None)
    if chat_id is None or mid is None:
        return None
    first_line = caption.splitlines()[0] if caption else ""
    return {
        "chat_id": chat_id, "message_id": mid,
        "file_id": getattr(media, "file_id", None), "file_unique_id": getattr(media, "file_unique_id", None),
        "file_name": name or first_line or None, "caption": caption[:1000], "size": getattr(media, "file_size", None),
        "mime": mime or None, "kind": kind, "duration": getattr(media, "duration", None),
        "date": getattr(msg, "date", None),
    }


class Indexer:
    def __init__(self, settings: Settings, db: Any, pipeline: IdentificationPipeline,
                 get_client: Callable[[], Any], catalog: Any):
        self.s = settings
        self.db = db
        self.pipeline = pipeline
        self.catalog = catalog
        self.get_client = get_client
        self.tasks: dict[str, asyncio.Task] = {}
        self.live_chats: set[int] = set()
        self._stopping = False

    # ---- sources ----------------------------------------------------------------------------
    async def refresh_sources(self) -> None:
        cur = self.db[dbm.SOURCES].find({"enabled": True, "live": {"$ne": False}}, {"_id": 1})
        self.live_chats = {d["_id"] async for d in cur}

    async def access_test(self, chat_id: int) -> tuple[bool, str]:
        client = self.get_client()
        try:
            await client.get_chat(chat_id)
            probe = await client.get_messages(chat_id, [1, 2, 3])
            if probe is None:
                return False, "cannot read messages"
            return True, "ok"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:160]}"

    async def add_source(self, chat_ref: int | str, added_by: int | None = None) -> dict:
        client = self.get_client()
        chat = await client.get_chat(chat_ref)
        ok, why = await self.access_test(chat.id)
        if not ok:
            raise SourceAccessError(f"cannot read {chat.title or chat.id}: {why}. Add the bot as admin/member first.")
        doc = {
            "title": getattr(chat, "title", None) or str(chat.id), "username": getattr(chat, "username", None),
            "type": str(getattr(chat, "type", "channel")).split(".")[-1].lower(), "enabled": True, "live": True,
            "added_by": added_by, "updated_at": now(),
        }
        await self.db[dbm.SOURCES].update_one(
            {"_id": chat.id}, {"$set": doc, "$setOnInsert": {"created_at": now(), "stats": {}, "last_message_id": 0}},
            upsert=True)
        await self.refresh_sources()
        return {"_id": chat.id, **doc}

    async def remove_source(self, chat_id: int, purge: bool = False) -> None:
        await self.cancel_jobs(chat_id)
        await self.db[dbm.SOURCES].delete_one({"_id": chat_id})
        if purge:
            await self.purge_source_files(chat_id)
        await self.refresh_sources()

    async def purge_source_files(self, chat_id: int) -> int:
        ids = await self.db[dbm.FILES].distinct("title_id", {"source_chat_id": chat_id})
        r = await self.db[dbm.FILES].update_many({"source_chat_id": chat_id},
                                                 {"$set": {"deleted": True, "updated_at": now()}})
        for tid in [i for i in ids if i]:
            await self.catalog.refresh_title_summary(tid)
        return r.modified_count

    async def set_source(self, chat_id: int, enabled: bool | None = None, live: bool | None = None) -> bool:
        upd: dict = {"updated_at": now()}
        if enabled is not None:
            upd["enabled"] = enabled
        if live is not None:
            upd["live"] = live
        r = await self.db[dbm.SOURCES].update_one({"_id": chat_id}, {"$set": upd})
        await self.refresh_sources()
        return bool(r.matched_count)

    # ---- ingestion -----------------------------------------------------------------------------
    async def ingest_message(self, msg: Any, reindex: bool = False, use_ai: bool = False) -> str:
        """Return one of: indexed | duplicate | skipped | failed."""
        meta = extract_media(msg, self.s.index_archives)
        if meta is None:
            return "skipped"
        fid = f"{meta['chat_id']}:{meta['message_id']}"
        try:
            existing = await self.db[dbm.FILES].find_one({"_id": fid}, {"_id": 1, "deleted": 1})
            if existing and not reindex:
                return "duplicate"
            if not existing and meta["file_unique_id"]:
                twin = await self.db[dbm.FILES].find_one(
                    {"file_unique_id": meta["file_unique_id"], "deleted": {"$ne": True}}, {"_id": 1})
                if twin:
                    await self.db[dbm.FILES].update_one(
                        {"_id": twin["_id"]},
                        {"$addToSet": {"alt_sources": {"chat_id": meta["chat_id"], "message_id": meta["message_id"],
                                                       "file_id": meta["file_id"]}}, "$inc": {"dup_count": 1}})
                    return "duplicate"
            ident = await self.pipeline.identify(meta["file_name"], meta["caption"], meta["file_unique_id"], use_ai=use_ai)
            if not existing:
                await self.db[dbm.FILES].insert_one({
                    "_id": fid, "source_chat_id": meta["chat_id"], "source_message_id": meta["message_id"],
                    "file_id": meta["file_id"], "file_unique_id": meta["file_unique_id"],
                    "file_name": meta["file_name"], "caption": meta["caption"], "size": meta["size"],
                    "mime": meta["mime"], "kind": meta["kind"], "duration": meta["duration"],
                    "message_date": meta["date"], "indexed_at": now(), "status": "pending_review",
                    "alt_sources": [], "deleted": False, "missing": False, "dup_count": 0,
                })
            else:
                await self.db[dbm.FILES].update_one({"_id": fid}, {"$set": {"deleted": False, "missing": False}})
            await self.pipeline.apply_to_file(fid, ident)
            return "indexed"
        except Exception as exc:
            if type(exc).__name__ == "DuplicateKeyError":
                return "duplicate"
            log.error("ingest failed for %s: %s: %s", fid, type(exc).__name__, str(exc)[:200])
            return "failed"

    async def _bump_source(self, chat_id: int, outcome: str, message_id: int | None = None) -> None:
        inc = {f"stats.{outcome}": 1}
        upd: dict = {"$inc": inc}
        if message_id:
            upd["$max"] = {"last_message_id": message_id}
        await self.db[dbm.SOURCES].update_one({"_id": chat_id}, upd)

    # ---- live ------------------------------------------------------------------------------------
    async def on_message(self, _client: Any, msg: Any) -> None:
        chat_id = getattr(getattr(msg, "chat", None), "id", None)
        if chat_id not in self.live_chats:
            return
        outcome = await self.ingest_message(msg)
        if outcome != "skipped":
            await self._bump_source(chat_id, outcome, getattr(msg, "id", None))

    async def on_deleted(self, _client: Any, messages: list) -> None:
        touched: set[str] = set()
        for m in messages:
            chat_id = getattr(getattr(m, "chat", None), "id", None)
            if chat_id not in self.live_chats:
                continue
            fid = f"{chat_id}:{m.id}"
            doc = await self.db[dbm.FILES].find_one({"_id": fid}, {"title_id": 1})
            if doc:
                await self.db[dbm.FILES].update_one({"_id": fid}, {"$set": {"deleted": True, "updated_at": now()}})
                if doc.get("title_id"):
                    touched.add(doc["title_id"])
        for tid in touched:
            await self.catalog.refresh_title_summary(tid)

    # ---- jobs ------------------------------------------------------------------------------------------
    async def start_job(self, chat_id: int, kind: str = "history", start_id: int = 1, end_id: int | None = None) -> str:
        if kind not in ("history", "reindex"):
            raise ValueError("kind must be history or reindex")
        active = await self.db[dbm.JOBS].find_one({"chat_id": chat_id, "status": {"$in": ["queued", "running", "paused"]}})
        if active:
            raise ValueError("a job is already active for this source")
        jid = uuid.uuid4().hex[:12]
        await self.db[dbm.JOBS].insert_one({
            "_id": jid, "chat_id": chat_id, "kind": kind, "status": "queued", "start_id": max(1, start_id),
            "end_id": end_id, "cursor": max(1, start_id) if kind == "history" else None,
            "processed": 0, "indexed": 0, "duplicates": 0, "skipped": 0, "failed": 0, "error": None,
            "created_at": now(), "updated_at": now(), "heartbeat": now(),
        })
        self._spawn(jid)
        return jid

    def _spawn(self, jid: str) -> None:
        t = self.tasks.get(jid)
        if t and not t.done():
            return
        self.tasks[jid] = asyncio.create_task(self._run_job(jid), name=f"index-{jid}")

    async def resume(self) -> int:
        """After a restart: re-queue every job that was queued/running."""
        await self.db[dbm.JOBS].update_many({"status": "running"}, {"$set": {"status": "queued", "updated_at": now()}})
        n = 0
        async for j in self.db[dbm.JOBS].find({"status": "queued"}, {"_id": 1}):
            self._spawn(j["_id"])
            n += 1
        if n:
            log.info("resumed %d indexing job(s)", n)
        return n

    async def pause_job(self, jid: str) -> bool:
        r = await self.db[dbm.JOBS].update_one({"_id": jid, "status": {"$in": ["queued", "running"]}},
                                               {"$set": {"status": "paused", "updated_at": now()}})
        return bool(r.matched_count)

    async def resume_job(self, jid: str) -> bool:
        r = await self.db[dbm.JOBS].update_one({"_id": jid, "status": {"$in": ["paused", "failed"]}},
                                               {"$set": {"status": "queued", "error": None, "updated_at": now()}})
        if r.matched_count:
            self._spawn(jid)
        return bool(r.matched_count)

    async def cancel_job(self, jid: str) -> bool:
        r = await self.db[dbm.JOBS].update_one({"_id": jid, "status": {"$in": ["queued", "running", "paused"]}},
                                               {"$set": {"status": "cancelled", "updated_at": now()}})
        return bool(r.matched_count)

    async def cancel_jobs(self, chat_id: int) -> None:
        await self.db[dbm.JOBS].update_many({"chat_id": chat_id, "status": {"$in": ["queued", "running", "paused"]}},
                                            {"$set": {"status": "cancelled", "updated_at": now()}})

    async def _status(self, jid: str) -> str | None:
        d = await self.db[dbm.JOBS].find_one({"_id": jid}, {"status": 1})
        return d["status"] if d else None

    async def _run_job(self, jid: str) -> None:
        job = await self.db[dbm.JOBS].find_one({"_id": jid})
        if not job or job["status"] not in ("queued", "running"):
            return
        await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"status": "running", "started_at": job.get("started_at") or now(),
                                                                   "updated_at": now()}})
        try:
            if job["kind"] == "reindex":
                await self._run_reindex(jid, job)
            else:
                await self._run_history(jid, job)
        except asyncio.CancelledError:
            # shutdown: leave the job "running" so resume() picks it up with its checkpoint
            raise
        except SourceAccessError as exc:
            await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"status": "failed", "error": str(exc)[:300],
                                                                       "updated_at": now()}})
        except Exception as exc:
            log.exception("indexing job %s crashed", jid)
            await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"status": "failed",
                                                                       "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                                                                       "updated_at": now()}})

    async def _fetch(self, chat_id: int, ids: list[int]) -> list:
        client = self.get_client()
        last: Exception | None = None
        for attempt in range(5):
            try:
                res = await client.get_messages(chat_id, ids)
                return list(res) if isinstance(res, (list, tuple)) else [res]
            except FloodWait as fw:
                wait = int(getattr(fw, "value", 5)) + 1
                log.warning("FloodWait %ss while indexing %s", wait, chat_id)
                await asyncio.sleep(wait)
                last = fw
            except Exception as exc:
                name = type(exc).__name__
                if name in ("ChannelInvalid", "ChannelPrivate", "PeerIdInvalid", "ChatAdminRequired", "ChatForbidden",
                            "UserBannedInChannel", "Forbidden"):
                    raise SourceAccessError(f"{name}: lost access to the source")
                last = exc
                await asyncio.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"fetch failed after retries: {type(last).__name__ if last else '?'}")

    async def probe_top_id(self, chat_id: int) -> int:
        """Highest existing message id. Works for user sessions and bots alike."""
        client = self.get_client()
        try:
            async for m in client.get_chat_history(chat_id, limit=1):
                return m.id
        except Exception:
            pass  # bots cannot call history: fall back to probing

        async def exists_above(x: int) -> bool:
            msgs = await self._fetch(chat_id, list(range(x + 1, x + 101)))
            return any(m is not None and not getattr(m, "empty", False) for m in msgs)

        known = (await self.db[dbm.SOURCES].find_one({"_id": chat_id}, {"last_message_id": 1}) or {}).get("last_message_id", 0)
        lo = known
        hi = max(lo, 100)
        while await exists_above(hi):
            lo, hi = hi, hi * 2 + 100
            if hi > 200_000_000:
                break
        while hi - lo > 100:  # binary search for the last block containing messages
            mid = (lo + hi) // 2
            if await exists_above(mid):
                lo = mid
            else:
                hi = mid
        top = lo
        for m in await self._fetch(chat_id, list(range(lo + 1, hi + 101))):
            if m is not None and not getattr(m, "empty", False):
                top = max(top, m.id)
        return top

    async def _run_history(self, jid: str, job: dict) -> None:
        chat_id = job["chat_id"]
        top = job.get("end_id") or await self.probe_top_id(chat_id)
        await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"end_id": top}})
        cursor = job.get("cursor") or job.get("start_id") or 1
        step = self.s.index_batch_size
        while cursor <= top and not self._stopping:
            st = await self._status(jid)
            if st != "running":
                return  # paused or cancelled: checkpoint is already saved
            ids = list(range(cursor, min(cursor + step, top + 1)))
            msgs = await self._fetch(chat_id, ids)
            counts = {"indexed": 0, "duplicate": 0, "skipped": 0, "failed": 0}
            for m in msgs:
                outcome = await self.ingest_message(m)
                counts[outcome] += 1
            cursor = ids[-1] + 1
            await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"cursor": cursor, "updated_at": now(), "heartbeat": now()},
                                                              "$inc": {"processed": len(ids), "indexed": counts["indexed"],
                                                                       "duplicates": counts["duplicate"], "skipped": counts["skipped"],
                                                                       "failed": counts["failed"]}})
            for k, v in counts.items():
                if v:
                    await self.db[dbm.SOURCES].update_one({"_id": chat_id}, {"$inc": {f"stats.{k}": v}})
            await self.db[dbm.SOURCES].update_one({"_id": chat_id}, {"$max": {"last_message_id": ids[-1]}})
            await asyncio.sleep(self.s.index_batch_delay)
        if cursor > top:
            await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"status": "done", "updated_at": now(), "finished_at": now()}})
            log.info("indexing job %s finished for %s", jid, chat_id)

    async def _run_reindex(self, jid: str, job: dict) -> None:
        """Re-run identification for stored files (no Telegram fetching)."""
        chat_id = job["chat_id"]
        last_id = job.get("cursor")
        while not self._stopping:
            if await self._status(jid) != "running":
                return
            f: dict = {"source_chat_id": chat_id, "deleted": {"$ne": True}}
            if last_id:
                f["_id"] = {"$gt": last_id}
            batch = [d async for d in self.db[dbm.FILES].find(f).sort("_id", 1).limit(50)]
            if not batch:
                break
            touched: set[str] = set()
            for d in batch:
                try:
                    ident = await self.pipeline.identify(d.get("file_name"), d.get("caption"), d.get("file_unique_id"), use_ai=False)
                    if d.get("title_id"):
                        touched.add(d["title_id"])
                    await self.pipeline.apply_to_file(d["_id"], ident)
                except Exception as exc:
                    log.error("reindex failed for %s: %s", d["_id"], exc)
                    await self.db[dbm.JOBS].update_one({"_id": jid}, {"$inc": {"failed": 1}})
            for tid in touched:
                await self.catalog.refresh_title_summary(tid)
                await self.catalog.prune_title_if_empty(tid)
            last_id = batch[-1]["_id"]
            await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"cursor": last_id, "updated_at": now(), "heartbeat": now()},
                                                              "$inc": {"processed": len(batch), "indexed": len(batch)}})
            await asyncio.sleep(0)
        else:
            return
        await self.db[dbm.JOBS].update_one({"_id": jid}, {"$set": {"status": "done", "updated_at": now(), "finished_at": now()}})

    async def supervisor(self) -> None:
        """Restart jobs whose task died or stalled (no heartbeat for 10 minutes)."""
        while True:
            try:
                await asyncio.sleep(60)
                stale = now().timestamp() - 600
                async for j in self.db[dbm.JOBS].find({"status": "running"}):
                    t = self.tasks.get(j["_id"])
                    hb = j.get("heartbeat")
                    hb_ts = hb.timestamp() if hasattr(hb, "timestamp") else 0
                    if (t is None or t.done()) or hb_ts < stale:
                        if t and not t.done():
                            t.cancel()
                        log.warning("restarting stalled indexing job %s", j["_id"])
                        self.tasks.pop(j["_id"], None)
                        self._spawn(j["_id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("supervisor error: %s", exc)

    async def shutdown(self) -> None:
        self._stopping = True
        for t in self.tasks.values():
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def list_jobs(self, limit: int = 30) -> list[dict]:
        cur = self.db[dbm.JOBS].find({}).sort("created_at", -1).limit(limit)
        return [{**d, "_id": d["_id"]} async for d in cur]


class AIQueueWorker:
    """Slowly feeds unresolved queue items through the AI fallback.

    Free OpenRouter models allow only a few requests per minute, so AI work
    happens here, decoupled from indexing speed. One AI attempt per distinct
    title key; success resolves every file that shares the key.
    """

    def __init__(self, db: Any, pipeline: IdentificationPipeline, ai: Any, interval: float = 20.0):
        self.db = db
        self.pipeline = pipeline
        self.ai = ai
        self.interval = interval
        self._stop = False

    async def step(self) -> bool:
        """Process one title group. Returns True if work was done."""
        if self.ai is None or not await self.ai.available():
            return False
        item = await self.db[dbm.QUEUE].find_one({"status": "pending", "ai_attempted": {"$ne": True}}, sort=[("created_at", 1)])
        if not item:
            return False
        fd = await self.db[dbm.FILES].find_one({"_id": item["file_id"]})
        key = item.get("title_key")
        if not fd:
            await self.db[dbm.QUEUE].delete_one({"_id": item["_id"]})
            return True
        ident = await self.pipeline.identify(fd.get("file_name"), fd.get("caption"), fd.get("file_unique_id"), use_ai=True)
        group = [d async for d in self.db[dbm.FILES].find(
            {"title_key": key, "status": {"$in": ["pending_review", "unidentified"]}, "deleted": {"$ne": True}})] if key else [fd]
        if ident.status == "identified":
            from ..corrections import parsed_from_dict
            for d in group or [fd]:
                p = parsed_from_dict(d.get("parsed")) if d["_id"] != fd["_id"] else ident.parsed
                one = type(ident)(ident.status, ident.method, p, title_id=ident.title_id, content_type=ident.content_type,
                                  confidence=ident.confidence, candidates=ident.candidates, ai=ident.ai,
                                  reason=ident.reason, overrides=ident.overrides)
                await self.pipeline.apply_to_file(d["_id"], one)
        else:
            for d in group or [fd]:
                await self.pipeline.apply_to_file(d["_id"], ident)
            await self.db[dbm.QUEUE].update_many({"file_id": {"$in": [d["_id"] for d in group or [fd]]}},
                                                 {"$set": {"ai_attempted": True}})
        return True

    async def run(self) -> None:
        while not self._stop:
            try:
                did = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("AI queue worker error: %s", exc)
                did = False
            await asyncio.sleep(2 if did else self.interval)

    def stop(self) -> None:
        self._stop = True
