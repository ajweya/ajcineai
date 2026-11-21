"""Async cache with Redis when configured, and a safe in-process fallback.

The interface is deliberately tiny so tests and single-instance deployments
work without Redis, while production uses Redis for shared state.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger("ajcine.cache")


class Cache:
    def __init__(self, redis_url: str = "", client: Any | None = None, prefix: str = "ajc:"):
        self.prefix = prefix
        self._redis = client
        self._url = redis_url
        self._mem: dict[str, tuple[float, str]] = {}
        self._mem_counters: dict[str, tuple[float, int]] = {}
        self.backend = "redis" if client is not None else "memory"

    async def connect(self) -> None:
        if self._redis is None and self._url:
            try:
                import redis.asyncio as aioredis

                r = aioredis.from_url(self._url, decode_responses=True)
                await r.ping()
                self._redis = r
                self.backend = "redis"
                log.info("cache backend: redis")
            except Exception as exc:
                log.error("redis unavailable (%s); using in-memory cache", type(exc).__name__)
                self._redis = None
                self.backend = "memory"

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass

    def _k(self, key: str) -> str:
        return self.prefix + key

    async def get(self, key: str) -> Any | None:
        k = self._k(key)
        try:
            if self._redis is not None:
                raw = await self._redis.get(k)
                return json.loads(raw) if raw is not None else None
        except Exception as exc:
            log.warning("cache get failed: %s", type(exc).__name__)
        item = self._mem.get(k)
        if not item:
            return None
        exp, raw = item
        if exp and exp < time.time():
            self._mem.pop(k, None)
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        k = self._k(key)
        raw = json.dumps(value, default=str)
        try:
            if self._redis is not None:
                await self._redis.set(k, raw, ex=ttl or None)
                return
        except Exception as exc:
            log.warning("cache set failed: %s", type(exc).__name__)
        if len(self._mem) > 20000:
            self._prune()
        self._mem[k] = ((time.time() + ttl) if ttl else 0, raw)

    async def delete(self, key: str) -> None:
        k = self._k(key)
        try:
            if self._redis is not None:
                await self._redis.delete(k)
        except Exception:
            pass
        self._mem.pop(k, None)

    async def incr(self, key: str, ttl: int = 60) -> int:
        """Atomic-ish counter with expiry; used for rate limiting."""
        k = self._k("ctr:" + key)
        try:
            if self._redis is not None:
                n = await self._redis.incr(k)
                if n == 1:
                    await self._redis.expire(k, ttl)
                return int(n)
        except Exception as exc:
            log.warning("cache incr failed: %s", type(exc).__name__)
        now = time.time()
        exp, n = self._mem_counters.get(k, (0, 0))
        if exp < now:
            exp, n = now + ttl, 0
        n += 1
        self._mem_counters[k] = (exp, n)
        return n

    async def clear(self) -> int:
        """Delete every key with our prefix. Returns number removed."""
        removed = 0
        try:
            if self._redis is not None:
                async for key in self._redis.scan_iter(match=self.prefix + "*", count=500):
                    await self._redis.delete(key)
                    removed += 1
        except Exception as exc:
            log.warning("cache clear failed: %s", type(exc).__name__)
        removed += len(self._mem) + len(self._mem_counters)
        self._mem.clear()
        self._mem_counters.clear()
        return removed

    async def ping(self) -> bool:
        if self._redis is None:
            return True  # in-memory fallback is always "up"
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    def _prune(self) -> None:
        now = time.time()
        for k in [k for k, (exp, _) in self._mem.items() if exp and exp < now]:
            self._mem.pop(k, None)
        if len(self._mem) > 20000:  # still too big: drop oldest half
            for k in list(self._mem)[: len(self._mem) // 2]:
                self._mem.pop(k, None)
