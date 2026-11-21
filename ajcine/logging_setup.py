"""Structured logging with an in-memory ring buffer the admin panels can read."""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import deque

_RING: deque[dict] = deque(maxlen=500)

_SECRET_MARKERS = ("api_key", "token", "password", "secret", "session", "hash")


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING.append(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created)),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": _scrub(record.getMessage()),
                }
            )
        except Exception:  # never let logging break the app
            pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": _scrub(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _scrub(text: str) -> str:
    """Redact obvious `key=value` secrets that could slip into messages."""
    low = text.lower()
    if not any(m in low for m in _SECRET_MARKERS):
        return text
    out = []
    for part in text.split(" "):
        k, sep, v = part.partition("=")
        if sep and any(m in k.lower() for m in _SECRET_MARKERS) and v:
            out.append(f"{k}=***")
        else:
            out.append(part)
    return " ".join(out)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(JsonFormatter())
    root.addHandler(sh)
    root.addHandler(RingHandler())
    for noisy in ("pyrogram", "pymongo", "asyncio", "httpx", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def recent_logs(limit: int = 100, level: str | None = None) -> list[dict]:
    items = list(_RING)
    if level:
        items = [i for i in items if i["level"] == level.upper()]
    return items[-limit:]
