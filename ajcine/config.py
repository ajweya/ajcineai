"""Environment-driven configuration. No credentials live in source code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(p.strip() for p in value.split(",") if p.strip())


def _int_csv(value: str | None) -> tuple[int, ...]:
    out = []
    for p in _csv(value):
        try:
            out.append(int(p))
        except ValueError:
            continue
    return tuple(out)


def read_env_file(path: str) -> dict[str, str]:
    """Parse a simple .env file: KEY=VALUE lines, `#` comments, optional `export `, optional single/double quotes."""
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not sep or not key.replace("_", "").isalnum():
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].rstrip()          # trailing comment on an unquoted value
        out[key] = val
    return out


@dataclass(frozen=True)
class Settings:
    # Telegram
    api_id: int = 0
    api_hash: str = ""
    bot_token: str = ""
    session_string: str = ""  # optional user session for historical indexing
    owner_ids: tuple[int, ...] = ()
    force_sub_channels: tuple[str, ...] = ()
    group_search: bool = True
    delivery_autodelete_minutes: int = 0
    protect_content: bool = False
    index_archives: bool = False

    # Storage
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "ajcineai"
    # Optional separate databases. Unset = that role uses MONGO_URI / MONGO_DB (single-database install).
    # Roles: catalog (movie/series details), files (file records), users (accounts), activity (searches,
    # deliveries, requests), core (settings, jobs, caches).
    mongo_catalog_uri: str = ""
    mongo_catalog_db: str = ""
    mongo_files_uri: str = ""
    mongo_files_db: str = ""
    mongo_users_uri: str = ""
    mongo_users_db: str = ""
    mongo_activity_uri: str = ""
    mongo_activity_db: str = ""
    mongo_ads_uri: str = ""
    mongo_ads_db: str = ""
    redis_url: str = ""  # empty => in-process cache (single instance only)

    # OpenRouter (free models only)
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    openrouter_fallback_models: tuple[str, ...] = ()
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_enabled: bool = True
    ai_max_retries: int = 3
    ai_rpm: int = 15  # free tier allows 20 requests/minute; keep headroom
    ai_backoff_base: float = 1.5
    ai_timeout: float = 45.0
    ai_cache_days: int = 30

    match_auto_threshold: float = 0.86
    match_review_threshold: float = 0.55

    # Delivery / security
    token_ttl_seconds: int = 900
    token_max_uses: int = 3
    rate_limit_per_minute: int = 20
    web_secret_key: str = ""
    web_session_hours: int = 12
    admin_bootstrap_user: str = "admin"
    admin_bootstrap_password: str = ""
    web_secure_cookies: bool = False

    # Advertising and broadcasts
    ads_enabled: bool = True                 # master switch (administrators can also switch ads off live)
    ads_bot_cooldown_minutes: int = 30       # at most one ad message per user in this period
    broadcast_rate_per_second: int = 20      # Telegram allows about 30 messages per second per bot

    # Collections
    auto_local_collections: bool = False     # group files TMDB cannot identify into one local (loc_) collection per title

    # TMDB: identifies titles (required) and supplies poster/backdrop URLs (images are never downloaded or stored)
    tmdb_api_key: str = ""                   # v3 API key
    tmdb_read_token: str = ""                # or the v4 "API Read Access Token"
    tmdb_api_base: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/"
    tmdb_poster_size: str = "w500"
    tmdb_backdrop_size: str = "w1280"
    tmdb_recheck_days: int = 7

    # Public website + streaming
    public_site_enabled: bool = True
    streaming_enabled: bool = False          # opt-in: streaming uses your server's bandwidth
    public_base_url: str = ""                # e.g. https://cine.example.com (used for Watch links in Telegram)
    stream_token_ttl_seconds: int = 6 * 3600
    stream_max_concurrent: int = 20
    stream_max_per_ip: int = 3
    public_rate_limit_per_minute: int = 240
    trust_proxy: bool = True                 # read the client IP from X-Forwarded-For (set false if exposed directly)

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000

    # Paths
    poster_dir: str = "data/posters"
    backup_dir: str = "data/backups"
    log_level: str = "INFO"

    # Indexing
    index_batch_size: int = 100
    index_batch_delay: float = 0.4

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        """Read settings from `env` (tests), or from a `.env` file plus the real environment (real variables win).

        The file is `./.env`, or the path in ENV_FILE. A missing file is fine (Docker/Koyeb pass real variables)."""
        e = env if env is not None else {**read_env_file(os.environ.get("ENV_FILE") or ".env"), **os.environ}
        g = e.get
        return cls(
            api_id=_int(g("API_ID"), 0),
            api_hash=g("API_HASH", ""),
            bot_token=g("BOT_TOKEN", ""),
            session_string=g("SESSION_STRING", ""),
            owner_ids=_int_csv(g("OWNER_IDS")),
            force_sub_channels=_csv(g("FORCE_SUB_CHANNELS")),
            group_search=_bool(g("GROUP_SEARCH"), True),
            delivery_autodelete_minutes=_int(g("DELIVERY_AUTODELETE_MINUTES"), 0),
            protect_content=_bool(g("PROTECT_CONTENT"), False),
            index_archives=_bool(g("INDEX_ARCHIVES"), False),
            mongo_uri=g("MONGO_URI", "mongodb://localhost:27017"),
            mongo_db=g("MONGO_DB", "ajcineai"),
            mongo_catalog_uri=g("MONGO_CATALOG_URI", "") or "", mongo_catalog_db=g("MONGO_CATALOG_DB", "") or "",
            mongo_files_uri=g("MONGO_FILES_URI", "") or "", mongo_files_db=g("MONGO_FILES_DB", "") or "",
            mongo_users_uri=g("MONGO_USERS_URI", "") or "", mongo_users_db=g("MONGO_USERS_DB", "") or "",
            mongo_activity_uri=g("MONGO_ACTIVITY_URI", "") or "", mongo_activity_db=g("MONGO_ACTIVITY_DB", "") or "",
            mongo_ads_uri=g("MONGO_ADS_URI", "") or "", mongo_ads_db=g("MONGO_ADS_DB", "") or "",
            redis_url=g("REDIS_URL", ""),
            openrouter_api_key=g("OPENROUTER_API_KEY", ""),
            openrouter_model=g("OPENROUTER_MODEL", ""),
            openrouter_fallback_models=_csv(g("OPENROUTER_FALLBACK_MODELS")),
            openrouter_base_url=g("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            ai_enabled=_bool(g("AI_ENABLED"), True),
            ai_max_retries=_int(g("AI_MAX_RETRIES"), 3),
            ai_rpm=max(1, min(20, _int(g("AI_RPM"), 15))),
            ai_backoff_base=_float(g("AI_BACKOFF_BASE"), 1.5),
            ai_timeout=_float(g("AI_TIMEOUT"), 45.0),
            ai_cache_days=_int(g("AI_CACHE_DAYS"), 30),
            match_auto_threshold=_float(g("MATCH_AUTO_THRESHOLD"), 0.86),
            match_review_threshold=_float(g("MATCH_REVIEW_THRESHOLD"), 0.55),
            token_ttl_seconds=_int(g("TOKEN_TTL_SECONDS"), 900),
            token_max_uses=_int(g("TOKEN_MAX_USES"), 3),
            rate_limit_per_minute=_int(g("RATE_LIMIT_PER_MINUTE"), 20),
            web_secret_key=g("WEB_SECRET_KEY", ""),
            web_session_hours=_int(g("WEB_SESSION_HOURS"), 12),
            admin_bootstrap_user=g("ADMIN_BOOTSTRAP_USER", "admin"),
            admin_bootstrap_password=g("ADMIN_BOOTSTRAP_PASSWORD", ""),
            web_secure_cookies=_bool(g("WEB_SECURE_COOKIES"), False),
            ads_enabled=_bool(g("ADS_ENABLED"), True),
            ads_bot_cooldown_minutes=max(1, _int(g("ADS_BOT_COOLDOWN_MINUTES"), 30)),
            broadcast_rate_per_second=max(1, min(30, _int(g("BROADCAST_RATE_PER_SECOND"), 20))),
            auto_local_collections=_bool(g("AUTO_LOCAL_COLLECTIONS"), False),
            tmdb_api_key=g("TMDB_API_KEY", "") or "",
            tmdb_read_token=g("TMDB_READ_TOKEN", "") or "",
            tmdb_api_base=(g("TMDB_API_BASE", "https://api.themoviedb.org/3") or "").rstrip("/"),
            tmdb_image_base=(g("TMDB_IMAGE_BASE", "https://image.tmdb.org/t/p/") or "").rstrip("/") + "/",
            tmdb_poster_size=g("TMDB_POSTER_SIZE", "w500") or "w500",
            tmdb_backdrop_size=g("TMDB_BACKDROP_SIZE", "w1280") or "w1280",
            tmdb_recheck_days=max(1, _int(g("TMDB_RECHECK_DAYS"), 7)),
            public_site_enabled=_bool(g("PUBLIC_SITE_ENABLED"), True),
            streaming_enabled=_bool(g("STREAMING_ENABLED"), False),
            public_base_url=(g("PUBLIC_BASE_URL", "") or "").rstrip("/"),
            stream_token_ttl_seconds=max(300, _int(g("STREAM_TOKEN_TTL_SECONDS"), 6 * 3600)),
            stream_max_concurrent=max(1, _int(g("STREAM_MAX_CONCURRENT"), 20)),
            stream_max_per_ip=max(1, _int(g("STREAM_MAX_PER_IP"), 3)),
            public_rate_limit_per_minute=max(5, _int(g("PUBLIC_RATE_LIMIT_PER_MINUTE"), 240)),
            trust_proxy=_bool(g("TRUST_PROXY"), True),
            web_host=g("WEB_HOST", "0.0.0.0"),
            web_port=_int(g("PORT") or g("WEB_PORT"), 8000),
            poster_dir=g("POSTER_DIR", "data/posters"),
            backup_dir=g("BACKUP_DIR", "data/backups"),
            log_level=g("LOG_LEVEL", "INFO"),
            index_batch_size=max(10, min(200, _int(g("INDEX_BATCH_SIZE"), 100))),
            index_batch_delay=_float(g("INDEX_BATCH_DELAY"), 0.4),
        )

    def db_targets(self) -> dict[str, tuple[str, str]]:
        """role -> (uri, database name). A role with its own URI but no name gets '<MONGO_DB>_<role>'."""
        out = {"core": (self.mongo_uri, self.mongo_db)}
        for role in ("catalog", "files", "users", "activity", "ads"):
            uri = getattr(self, f"mongo_{role}_uri")
            name = getattr(self, f"mongo_{role}_db")
            out[role] = (uri or self.mongo_uri, name or (f"{self.mongo_db}_{role}" if uri else self.mongo_db))
        return out

    @property
    def tmdb_enabled(self) -> bool:
        return bool(self.tmdb_api_key or self.tmdb_read_token)

    def problems(self, need_telegram: bool = True) -> list[str]:
        """Return human-readable configuration problems (empty list = OK)."""
        out: list[str] = []
        if need_telegram:
            if not self.api_id or not self.api_hash:
                out.append("API_ID and API_HASH are required (https://my.telegram.org)")
            if not self.bot_token:
                out.append("BOT_TOKEN is required (create one with @BotFather)")
            if not self.owner_ids:
                out.append("OWNER_IDS must contain at least one Telegram user id")
        if len(self.web_secret_key) < 32:
            out.append("WEB_SECRET_KEY must be at least 32 characters")
        if self.public_base_url and not self.public_base_url.startswith(("https://", "http://")):
            out.append("PUBLIC_BASE_URL must start with https:// (or http:// for local testing)")
        for role, (uri, _n) in self.db_targets().items():
            if not uri.startswith(("mongodb://", "mongodb+srv://")):
                out.append(f"the MongoDB URI for '{role}' must start with mongodb:// or mongodb+srv://")
        if not self.tmdb_enabled:
            out.append("TMDB_API_KEY or TMDB_READ_TOKEN is required: titles are identified through TMDB "
                       "(free key: https://www.themoviedb.org/settings/api)")
        if self.tmdb_enabled and not self.tmdb_image_base.startswith("https://"):
            out.append("TMDB_IMAGE_BASE must start with https://")
        if self.streaming_enabled and not self.public_site_enabled:
            out.append("STREAMING_ENABLED requires PUBLIC_SITE_ENABLED=true")
        if self.match_review_threshold >= self.match_auto_threshold:
            out.append("MATCH_REVIEW_THRESHOLD must be lower than MATCH_AUTO_THRESHOLD")
        return out

    def with_overrides(self, **kw) -> "Settings":
        return replace(self, **kw)
