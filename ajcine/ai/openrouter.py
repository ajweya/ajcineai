"""OpenRouter gateway restricted to FREE models.

Guarantees:
  * a model is only ever called if it is verified free (zero prompt+completion
    pricing in the live catalogue, or - if the catalogue is unreachable - its
    id ends with ":free");
  * the native OpenRouter `models` fallback array is never sent (it can route
    to paid models); fallbacks are tried by this client, one verified-free
    model at a time;
  * free-tier limits (20 req/min, 50 or 1000 req/day) are respected with a
    local throttle, Retry-After handling, exponential backoff and circuit
    breakers;
  * results are cached so the same input never costs a second request.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from .. import db as dbm

log = logging.getLogger("ajcine.ai")

CONFIG_KEY = "ai_config"
FREE_ROUTER_ID = "openrouter/free"


class AIError(Exception):
    pass


class AIUnavailable(AIError):
    """AI is disabled, unconfigured, rate-limited, or every free model failed."""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _price_is_zero(v: Any) -> bool | None:
    if v is None:
        return None
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return None


class OpenRouterClient:
    def __init__(self, settings: Settings, db: Any, cache: Any, session: Any | None = None,
                 sleep=asyncio.sleep, clock=time.time):
        self.s = settings
        self.db = db
        self.cache = cache
        self._session = session
        self._sleep = sleep
        self._clock = clock
        self._catalog: dict[str, dict] = {}
        self._catalog_at = 0.0
        self._catalog_ok = False
        self._calls: deque[float] = deque()
        self._breaker: dict[str, dict] = {}
        self._daily_exhausted_day: str | None = None
        self._halt_until = 0.0  # global stop (auth / credits problems)
        self._lock = asyncio.Lock()

    # ---- http --------------------------------------------------------------------
    async def _http(self):
        if self._session is None or getattr(self._session, "closed", False):
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.s.ai_timeout))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not getattr(self._session, "closed", True):
            await self._session.close()

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "X-Title": "AJ CINE AI"}
        if self.s.openrouter_api_key:
            h["Authorization"] = f"Bearer {self.s.openrouter_api_key}"
        return h

    # ---- free-model validation ----------------------------------------------------------
    async def refresh_catalog(self, force: bool = False) -> bool:
        if not force and self._catalog and self._clock() - self._catalog_at < 3600:
            return self._catalog_ok
        try:
            http = await self._http()
            async with http.get(self.s.openrouter_base_url + "/models", headers=self._headers()) as r:
                if r.status != 200:
                    raise AIError(f"catalogue HTTP {r.status}")
                payload = await r.json()
            self._catalog = {m["id"]: m for m in payload.get("data", []) if "id" in m}
            self._catalog_at = self._clock()
            self._catalog_ok = bool(self._catalog)
        except Exception as exc:
            log.warning("model catalogue unavailable: %s", type(exc).__name__)
            self._catalog_ok = False
        return self._catalog_ok

    def is_free(self, model_id: str) -> bool:
        """Fail-closed free check."""
        if not model_id:
            return False
        if self._catalog_ok:
            m = self._catalog.get(model_id)
            if not m:
                return False
            pr = m.get("pricing") or {}
            prompt, comp = _price_is_zero(pr.get("prompt")), _price_is_zero(pr.get("completion"))
            if prompt is None or comp is None:
                return False
            for extra in ("request", "image", "web_search", "internal_reasoning"):
                z = _price_is_zero(pr.get(extra))
                if z is False:
                    return False
            return prompt and comp
        # Catalogue unreachable: only trust the explicit free markers.
        return model_id.endswith(":free") or model_id == FREE_ROUTER_ID

    async def free_models(self, limit: int = 60) -> list[str]:
        await self.refresh_catalog()
        ids = [i for i in self._catalog if self.is_free(i)]
        return sorted(ids)[:limit]

    # ---- configuration (env defaults, admin overrides persisted in Mongo) ------------------
    async def config(self) -> dict:
        override = await dbm.get_setting(self.db, CONFIG_KEY, {}) or {}
        return {
            "enabled": override.get("enabled", self.s.ai_enabled),
            "model": override.get("model") or self.s.openrouter_model,
            "fallbacks": override.get("fallbacks") if override.get("fallbacks") is not None
            else list(self.s.openrouter_fallback_models),
        }

    async def set_config(self, enabled: bool | None = None, model: str | None = None,
                         fallbacks: list[str] | None = None) -> dict:
        await self.refresh_catalog()
        cur = await dbm.get_setting(self.db, CONFIG_KEY, {}) or {}
        if model is not None:
            if model and not self.is_free(model):
                raise ValueError(f"{model!r} is not a verified free model")
            cur["model"] = model
        if fallbacks is not None:
            bad = [m for m in fallbacks if not self.is_free(m)]
            if bad:
                raise ValueError(f"not verified free: {', '.join(bad)}")
            cur["fallbacks"] = fallbacks
        if enabled is not None:
            cur["enabled"] = bool(enabled)
        await dbm.set_setting(self.db, CONFIG_KEY, cur)
        return await self.config()

    async def enabled(self) -> bool:
        cfg = await self.config()
        return bool(cfg["enabled"] and self.s.openrouter_api_key)

    async def active_models(self) -> list[str]:
        await self.refresh_catalog()
        cfg = await self.config()
        chain = [m for m in [cfg["model"], *cfg["fallbacks"]] if m]
        out = []
        for m in dict.fromkeys(chain):
            if self.is_free(m):
                out.append(m)
            else:
                log.error("configured model %s is NOT free and will never be used", m)
        return out

    # ---- throttle / breaker ----------------------------------------------------------------
    async def _throttle(self, max_wait: float) -> bool:
        rpm = max(1, getattr(self.s, "ai_rpm", 15))
        deadline = self._clock() + max_wait
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) < rpm:
                self._calls.append(now)
                return True
            wait = 60 - (now - self._calls[0]) + 0.05
            if now + wait > deadline:
                return False
            await self._sleep(wait)

    def _breaker_open(self, model: str) -> bool:
        b = self._breaker.get(model)
        return bool(b and b["until"] > self._clock())

    def _breaker_fail(self, model: str) -> None:
        b = self._breaker.setdefault(model, {"fails": 0, "until": 0.0})
        b["fails"] += 1
        if b["fails"] >= 3:
            b["until"] = self._clock() + min(900, 30 * 2 ** (b["fails"] - 3))

    def _breaker_ok(self, model: str) -> None:
        self._breaker.pop(model, None)

    # ---- stats -------------------------------------------------------------------------------
    async def _stat(self, model: str, **inc: int) -> None:
        try:
            for m in (model, "_all"):
                await self.db[dbm.AI_USAGE].update_one(
                    {"day": _today(), "model": m}, {"$inc": inc}, upsert=True)
        except Exception:
            pass

    async def usage(self, days: int = 7) -> list[dict]:
        cur = self.db[dbm.AI_USAGE].find({}, {"_id": 0}).sort("day", -1).limit(days * 12)
        return [d async for d in cur]

    # ---- main entry --------------------------------------------------------------------------
    @staticmethod
    def _cache_key(task: str, payload: Any) -> str:
        raw = task + "|" + json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def complete_json(self, task: str, system: str, user: str, payload: Any,
                            max_wait: float = 20.0, max_tokens: int = 500) -> tuple[dict, str]:
        """Return (parsed_json, model_used). Raises AIUnavailable when impossible."""
        key = self._cache_key(task, payload)
        hit = await self.cache.get("ai:" + key)
        if hit is None:
            doc = await self.db[dbm.AI_CACHE].find_one({"key": key})
            if doc:
                hit = doc["value"]
                await self.cache.set("ai:" + key, hit, ttl=3600)
        if hit is not None:
            await self._stat("cache", cache_hits=1)
            return hit["data"], hit.get("model", "cache")

        if not self.s.openrouter_api_key:
            raise AIUnavailable("OPENROUTER_API_KEY is not set")
        if not await self.enabled():
            raise AIUnavailable("AI is disabled")
        if self._clock() < self._halt_until:
            raise AIUnavailable("AI halted after an authentication/credit error")
        if self._daily_exhausted_day == _today():
            raise AIUnavailable("free-model daily request limit reached")
        models = await self.active_models()
        if not models:
            raise AIUnavailable("no verified free model configured")

        last_err = "no model attempted"
        for idx, model in enumerate(models):
            if self._breaker_open(model):
                last_err = f"{model}: circuit open"
                continue
            try:
                data = await self._call_model(model, system, user, max_wait, max_tokens)
            except _ModelFailed as exc:
                last_err = f"{model}: {exc}"
                await self._stat(model, errors=1, **({"fallbacks": 1} if idx + 1 < len(models) else {}))
                self._breaker_fail(model)
                continue
            self._breaker_ok(model)
            value = {"data": data, "model": model}
            await self.cache.set("ai:" + key, value, ttl=3600)
            await self.db[dbm.AI_CACHE].update_one(
                {"key": key}, {"$set": {"key": key, "value": value, "created_at": datetime.now(timezone.utc)}},
                upsert=True)
            return data, model
        raise AIUnavailable(f"all free models failed ({last_err})")

    async def _call_model(self, model: str, system: str, user: str, max_wait: float, max_tokens: int) -> dict:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        attempts = max(1, self.s.ai_max_retries)
        use_format = True
        for attempt in range(attempts):
            if not await self._throttle(max_wait):
                raise _ModelFailed("local rate limit (free tier: 20 req/min)")
            if not use_format:
                body.pop("response_format", None)
            t0 = self._clock()
            await self._stat(model, requests=1)
            try:
                http = await self._http()
                async with http.post(self.s.openrouter_base_url + "/chat/completions",
                                     headers=self._headers(), json=body) as r:
                    status = r.status
                    retry_after = _retry_after(r.headers.get("Retry-After"))
                    try:
                        payload = await r.json(content_type=None)
                    except Exception:
                        payload = {}
            except (asyncio.TimeoutError, OSError) as exc:
                await self._backoff(attempt, None)
                if attempt + 1 == attempts:
                    raise _ModelFailed(f"network error {type(exc).__name__}")
                continue
            except Exception as exc:  # aiohttp.ClientError and friends
                await self._backoff(attempt, None)
                if attempt + 1 == attempts:
                    raise _ModelFailed(f"client error {type(exc).__name__}")
                continue

            err = payload.get("error") if isinstance(payload, dict) else None
            msg = (err or {}).get("message", "") if isinstance(err, dict) else ""
            if status == 200 and not err:
                try:
                    content = payload["choices"][0]["message"]["content"]
                    data = extract_json(content)
                except (KeyError, IndexError, TypeError, AIError):
                    if attempt + 1 == attempts:
                        raise _ModelFailed("unusable response")
                    await self._backoff(attempt, None)
                    continue
                usage = (payload.get("usage") or {}).get("total_tokens") or 0
                await self._stat(model, success=1, latency_ms=int((self._clock() - t0) * 1000), tokens=int(usage))
                return data
            if status == 429:
                await self._stat(model, rate_limited=1)
                if "per-day" in msg.lower() or "per day" in msg.lower() or "daily" in msg.lower():
                    self._daily_exhausted_day = _today()
                    raise AIUnavailable("free-model daily request limit reached")
                if attempt + 1 == attempts:
                    raise _ModelFailed("rate limited (429)")
                await self._backoff(attempt, retry_after)
                continue
            if status in (401, 403):
                self._halt_until = self._clock() + 600
                raise AIUnavailable(f"authentication rejected ({status})")
            if status == 402:
                self._halt_until = self._clock() + 600
                raise AIUnavailable("OpenRouter reported insufficient credits (402); not retrying")
            if status == 400 and use_format:
                use_format = False  # some free models reject response_format
                continue
            if status in (408, 500, 502, 503, 504) or (status == 200 and err):
                if attempt + 1 == attempts:
                    raise _ModelFailed(f"provider error {status}")
                await self._backoff(attempt, retry_after)
                continue
            raise _ModelFailed(f"HTTP {status}")  # 400/404/...: try next model
        raise _ModelFailed("exhausted retries")

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after else self.s.ai_backoff_base * (2 ** attempt)
        delay = min(30.0, delay) + random.uniform(0, 0.4)
        await self._sleep(delay)

    # ---- diagnostics -----------------------------------------------------------------------------
    async def status(self) -> dict:
        await self.refresh_catalog()
        cfg = await self.config()
        models = []
        for m in [cfg["model"], *cfg["fallbacks"]]:
            if not m:
                continue
            models.append({
                "id": m, "verified_free": self.is_free(m),
                "circuit_open": self._breaker_open(m),
                "role": "primary" if m == cfg["model"] else "fallback",
            })
        today = await self.db[dbm.AI_USAGE].find_one({"day": _today(), "model": "_all"}, {"_id": 0}) or {}
        return {
            "key_configured": bool(self.s.openrouter_api_key),
            "enabled": cfg["enabled"],
            "effective_enabled": bool(cfg["enabled"] and self.s.openrouter_api_key),
            "catalogue_reachable": self._catalog_ok,
            "models": models,
            "daily_limit_reached": self._daily_exhausted_day == _today(),
            "today": today,
        }

    async def test(self) -> dict:
        try:
            data, model = await self.complete_json(
                "selftest", 'Reply with only {"ok": true}', 'Return {"ok": true}', {"t": int(self._clock() // 60)},
                max_wait=5, max_tokens=20)
            return {"ok": bool(data.get("ok")), "model": model}
        except AIError as exc:
            return {"ok": False, "error": str(exc)}


class _ModelFailed(Exception):
    pass


def _retry_after(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str | None) -> dict:
    """Parse a JSON object from model output, tolerating fences and prose."""
    if not text or not isinstance(text, str):
        raise AIError("empty response")
    t = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b <= a:
            raise AIError("no JSON object in response")
        try:
            obj = json.loads(t[a:b + 1])
        except ValueError as exc:
            raise AIError("invalid JSON") from exc
    if not isinstance(obj, dict):
        raise AIError("JSON is not an object")
    return obj
