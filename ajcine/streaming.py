
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from starlette.responses import StreamingResponse

log = logging.getLogger("ajcine.streaming")

CHUNK = 1024 * 1024
PREFETCH = 4
_GONE = {"MessageIdInvalid", "MessageEmpty", "ChannelPrivate", "ChannelInvalid", "ChatForbidden", "PeerIdInvalid",
         "ChatAdminRequired", "UserBannedInChannel", "Forbidden"}


class RangeError(Exception):
    """Requested range cannot be satisfied (HTTP 416)."""


class StreamBusy(Exception):
    """Too many concurrent streams (global or per client address)."""


class SourceGone(Exception):
    """The Telegram message or media no longer exists / is unreadable."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single `bytes=` range. Returns inclusive (start, end), or None to serve the whole file.

    Malformed headers are ignored (RFC 9110: a server may ignore Range); multi-range requests are answered
    with the first range; unsatisfiable ranges raise RangeError.
    """
    if not header or size <= 0:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(.+?)\s*", header, re.I)
    if not m:
        return None
    first = m.group(1).split(",")[0].strip()
    m2 = re.fullmatch(r"(\d*)\s*-\s*(\d*)", first)
    if not m2:
        return None
    a, b = m2.groups()
    if a == "" and b == "":
        return None
    if a == "":                                   # suffix range: last N bytes
        n = int(b)
        if n == 0:
            raise RangeError
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size:
        raise RangeError
    if b and end < start:
        return None
    return start, min(end, size - 1)


def browser_hint(f: dict) -> str:
    """How likely the file is to play in a browser: good | maybe | poor (a hint, never a promise)."""
    ext = os.path.splitext(f.get("file_name") or "")[1].lstrip(".").lower()
    if ext in ("avi", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts", "3gp", "mts", "rmvb"):
        return "poor"
    level = 0
    if ext in ("mp4", "m4v", "mov", "webm"):
        level = 0
    elif ext == "mkv":
        level = 1
    else:
        level = 1
    if f.get("video_codec") in ("HEVC", "AV1", "XviD", "DivX"):
        level = max(level, 1)
    if any(k in (f.get("audio_codec") or "").upper() for k in ("DTS", "TRUEHD", "AC3", "DDP", "EAC3")):
        level = max(level, 1)
    return ("good", "maybe")[level]


def guess_mime(f: dict, media_mime: str | None) -> str:
    ext = os.path.splitext(f.get("file_name") or "")[1].lower()
    if ext == ".mkv":
        return "video/x-matroska"
    if media_mime and media_mime.startswith(("video/", "audio/")):
        return media_mime
    return mimetypes.guess_type(f.get("file_name") or "")[0] or "application/octet-stream"


def content_disposition(name: str | None, attachment: bool = False) -> str:
    safe = re.sub(r'[\x00-\x1f"\\/]', "_", name or "video")[:150]
    ascii_name = safe.encode("ascii", "replace").decode().replace("?", "_")
    from urllib.parse import quote
    return f"{'attachment' if attachment else 'inline'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe)}"


# ----------------------------------------------------------------------- range reader
async def iter_range(client: Any, message: Any, start: int, end: int, prefetch: int = PREFETCH) -> AsyncIterator[bytes]:
    """Yield exactly bytes[start..end] of the message's media."""
    first = start // CHUNK
    limit = end // CHUNK - first + 1
    skip = start - first * CHUNK
    remaining = end - start + 1
    q: asyncio.Queue = asyncio.Queue(maxsize=max(1, prefetch))

    async def producer() -> None:
        try:
            async for chunk in client.stream_media(message, limit=limit, offset=first):
                await q.put(chunk)
            await q.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surfaced to the consumer
            await q.put(exc)

    task = asyncio.create_task(producer(), name="tg-stream-producer")
    try:
        while remaining > 0:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            if skip:
                item, skip = item[skip:], 0
            if len(item) > remaining:
                item = item[:remaining]
            remaining -= len(item)
            if item:
                yield item
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ----------------------------------------------------------------------- limits
class StreamLimiter:
    """Caps concurrent streams globally and per client address."""

    def __init__(self, max_total: int, max_per_ip: int):
        self.max_total, self.max_per_ip = max_total, max_per_ip
        self.total = 0
        self.per_ip: dict[str, int] = {}

    def acquire(self, ip: str) -> None:
        if self.total >= self.max_total or self.per_ip.get(ip, 0) >= self.max_per_ip:
            raise StreamBusy()
        self.total += 1
        self.per_ip[ip] = self.per_ip.get(ip, 0) + 1

    def release(self, ip: str) -> None:
        self.total = max(0, self.total - 1)
        n = self.per_ip.get(ip, 0) - 1
        if n <= 0:
            self.per_ip.pop(ip, None)
        else:
            self.per_ip[ip] = n


# ----------------------------------------------------------------------- source resolution
@dataclass
class Resolved:
    client: Any
    message: Any
    size: int
    mime: str | None
    chat_id: int
    message_id: int


class MediaResolver:
    """Finds a readable Telegram message for an indexed file (primary source, then duplicates)."""

    def __init__(self, get_clients, ttl: float = 600.0, max_items: int = 512):
        self._get_clients = get_clients
        self._ttl, self._max = ttl, max_items
        self._cache: dict[tuple, tuple[float, Resolved]] = {}

    @staticmethod
    def _media(msg: Any) -> Any | None:
        for attr in ("video", "document", "audio"):
            m = getattr(msg, attr, None)
            if m is not None:
                return m
        return None

    async def resolve(self, f: dict) -> Resolved:
        sources = [(f["source_chat_id"], f["source_message_id"])]
        sources += [(a["chat_id"], a["message_id"]) for a in f.get("alt_sources", [])]
        now = time.time()
        for key in sources:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        last_flood = None
        for chat_id, mid in sources:
            for client in self._get_clients():
                try:
                    msg = await client.get_messages(chat_id, mid)
                except Exception as exc:
                    name = type(exc).__name__
                    if name == "FloodWait":
                        last_flood = exc
                    elif name not in _GONE:
                        log.warning("get_messages failed for %s:%s: %s", chat_id, mid, name)
                    continue
                if msg is None or getattr(msg, "empty", False):
                    continue
                media = self._media(msg)
                if media is None:
                    continue
                size = int(getattr(media, "file_size", 0) or 0)
                if size <= 0:
                    continue
                res = Resolved(client, msg, size, getattr(media, "mime_type", None), chat_id, mid)
                if len(self._cache) >= self._max:
                    for k in list(self._cache)[: self._max // 4]:
                        self._cache.pop(k, None)
                self._cache[(chat_id, mid)] = (now + self._ttl, res)
                return res
        if last_flood is not None:
            raise StreamBusy()
        raise SourceGone()

    def forget(self, f: dict) -> None:
        self._cache.pop((f["source_chat_id"], f["source_message_id"]), None)


class GuardedStreamingResponse(StreamingResponse):
    """Always runs `cleanup` (closing the Telegram reader, releasing limits), even if the client disconnects."""

    def __init__(self, content, cleanup, **kw):
        super().__init__(content, **kw)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._cleanup()
