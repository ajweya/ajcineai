from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ajcine.db")

# Collection names (single source of truth)
USERS = "users"
ADMINS = "administrators"
SOURCES = "source_channels"
MOVIES = "movies"
SERIES = "series"
SEASONS = "seasons"
EPISODES = "episodes"
FILES = "media_files"
LANGUAGES = "languages"
QUEUE = "identification_queue"
TITLE_CACHE = "title_cache"
CORRECTIONS = "manual_corrections"
TITLE_DECISIONS = "title_decisions"
SEARCH_HISTORY = "search_history"
DELIVERIES = "delivery_history"
DELIVERY_TOKENS = "delivery_tokens"
JOBS = "indexing_jobs"
SETTINGS = "system_settings"
AI_CACHE = "ai_cache"
AI_USAGE = "ai_usage"
WEB_SESSIONS = "web_sessions"
REQUESTS = "title_requests"
ADS = "ads"
BROADCASTS = "broadcasts"
BOT_CHATS = "bot_chats"
WATCHLIST = "watchlist"

# Which database each collection lives in. With no extra MONGO_*_URI / MONGO_*_DB settings every role points at the
# one default database, so a single-database install behaves exactly as before.
ROLES: dict[str, tuple[str, ...]] = {
    "catalog": (MOVIES, SERIES, SEASONS, EPISODES, LANGUAGES),                # movie/series details
    "files": (FILES, QUEUE, CORRECTIONS, TITLE_DECISIONS),                    # file records and review state
    "users": (USERS, ADMINS, WEB_SESSIONS, DELIVERY_TOKENS, BOT_CHATS, WATCHLIST),   # users, groups the bot is in, watchlists
    "activity": (SEARCH_HISTORY, DELIVERIES, AI_USAGE, REQUESTS),             # searches, deliveries, usage, requests
    "ads": (ADS, BROADCASTS),                                                 # advertisements and broadcasts
    "core": (SOURCES, JOBS, SETTINGS, TITLE_CACHE, AI_CACHE),                  # configuration, jobs, caches
}
COLLECTION_ROLE = {c: r for r, cs in ROLES.items() for c in cs}


class MultiDB:
    """Routes `db[collection]` to the database that owns that collection (see ROLES).

    Behaves like a single Motor database for the rest of the code. Nothing is shared between databases (no
    cross-database queries or transactions), so each can live on a different server or cluster.
    """

    def __init__(self, dbs: dict[str, Any], targets: dict[str, tuple[str, str]] | None = None):
        missing = set(ROLES) - set(dbs)
        if missing:
            raise ValueError(f"missing database roles: {sorted(missing)}")
        self.dbs = dbs
        self.targets = targets or {}

    def __getitem__(self, name: str) -> Any:
        return self.dbs[COLLECTION_ROLE.get(name, "core")][name]

    def __getattr__(self, name: str) -> Any:     # command(), name, ... -> the core database
        if name.startswith("__") or name in ("dbs", "targets"):
            raise AttributeError(name)
        return getattr(self.dbs["core"], name)

    async def ping(self) -> dict[str, bool]:
        out = {}
        for role, d in self.dbs.items():
            try:
                await d.command("ping")
                out[role] = True
            except Exception:
                out[role] = False
        return out

    def describe(self) -> list[dict]:
        """Layout for the dashboard. Credentials are never included: only host and database name."""
        from urllib.parse import urlparse
        rows = []
        for role, d in self.dbs.items():
            uri, name = self.targets.get(role, ("", getattr(d, "name", "")))
            host = urlparse(uri).hostname if uri else None
            rows.append({"role": role, "database": getattr(d, "name", name), "host": host or "-",
                         "collections": list(ROLES[role])})
        return rows


async def connect(uri: str, name: str) -> Any:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000, tz_aware=True)
    await client.admin.command("ping")
    return client[name]


async def connect_multi(settings: Any) -> MultiDB:
    """One Motor client per distinct URI; one database per role."""
    from motor.motor_asyncio import AsyncIOMotorClient

    targets = settings.db_targets()
    clients: dict[str, Any] = {}
    dbs: dict[str, Any] = {}
    for role, (uri, name) in targets.items():
        if uri not in clients:
            c = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000, tz_aware=True)
            try:
                await c.admin.command("ping")
            except Exception as exc:
                raise RuntimeError(f"MongoDB for role '{role}' is not reachable ({type(exc).__name__}); "
                                   f"check MONGO_{role.upper()}_URI / MONGO_URI") from exc
            clients[uri] = c
        dbs[role] = clients[uri][name]
    return MultiDB(dbs, targets)


async def ensure_indexes(db: Any) -> None:
    """Create all indexes. Safe to call on every start."""

    async def idx(coll: str, keys, **kw):
        try:
            await db[coll].create_index(keys, **kw)
        except Exception as exc:  # existing index with different options etc.
            log.warning("index %s %s skipped: %s", coll, keys, exc)

    await idx(USERS, "last_seen")
    await idx(ADMINS, "username", unique=True, sparse=True)
    await idx(ADMINS, "telegram_id", sparse=True)
    await idx(SOURCES, "enabled")

    for coll in (MOVIES, SERIES):
        await idx(coll, "search_tokens")
        await idx(coll, [("file_count", -1), ("popularity", -1)])
        await idx(coll, "year")
        await idx(coll, "last_indexed_at")
        await idx(coll, "languages")
        await idx(coll, "match_key")

    await idx(SEASONS, [("series_id", 1), ("season", 1)], unique=True)
    await idx(EPISODES, [("series_id", 1), ("season", 1), ("episode", 1)], unique=True)

    await idx(FILES, [("source_chat_id", 1), ("source_message_id", 1)], unique=True)
    await idx(FILES, "file_unique_id")
    await idx(FILES, "collection_id")
    await idx(FILES, "file_name")
    await idx(FILES, [("collection_id", 1), ("season", 1), ("episode", 1), ("quality", 1)])
    await idx(FILES, [("collection_id", 1), ("quality", 1), ("languages", 1)])
    # One live record per physical upload: a second chat holding the same file is stored as an alt source.
    await idx(FILES, "file_unique_id", unique=True, name="uniq_live_file_unique_id",
              partialFilterExpression={"deleted": False, "file_unique_id": {"$type": "string"}})
    await idx(FILES, [("title_id", 1), ("season", 1), ("episode", 1)])
    await idx(FILES, [("title_id", 1), ("languages", 1), ("quality", 1)])
    await idx(FILES, "status")
    await idx(FILES, "title_key")
    await idx(FILES, "dup_key")
    await idx(FILES, "indexed_at")
    await idx(FILES, "deleted")

    await idx(LANGUAGES, "code", unique=True)
    await idx(QUEUE, [("status", 1), ("created_at", 1)])
    await idx(QUEUE, "file_id", unique=True)
    await idx(QUEUE, "title_key")
    await idx(TITLE_CACHE, "key", unique=True)
    await idx(TITLE_CACHE, "created_at", expireAfterSeconds=60 * 60 * 24 * 30)
    await idx(CORRECTIONS, "file_unique_id", sparse=True)
    await idx(CORRECTIONS, "title_key", sparse=True)
    await idx(TITLE_DECISIONS, "title_key", unique=True)
    await idx(SEARCH_HISTORY, [("user_id", 1), ("ts", -1)])
    await idx(SEARCH_HISTORY, "norm_query")
    await idx(DELIVERIES, [("user_id", 1), ("ts", -1)])
    await idx(DELIVERIES, "title_id")
    await idx(DELIVERY_TOKENS, "expires_at", expireAfterSeconds=0)
    await idx(JOBS, [("status", 1), ("updated_at", -1)])
    await idx(SETTINGS, "key", unique=True)
    await idx(AI_CACHE, "key", unique=True)
    await idx(AI_CACHE, "created_at", expireAfterSeconds=60 * 60 * 24 * 30)
    await idx(AI_USAGE, [("day", 1), ("model", 1)], unique=True)
    await idx(WEB_SESSIONS, "expires_at", expireAfterSeconds=0)
    await idx(ADS, [("active", 1), ("placements", 1)])
    await idx(BROADCASTS, [("status", 1), ("created_at", -1)])
    await idx(BOT_CHATS, [("active", 1), ("type", 1)])
    await idx(WATCHLIST, [("user_id", 1), ("title_id", 1)], unique=True)
    await idx(REQUESTS, "key", unique=True)
    await idx(REQUESTS, [("status", 1), ("votes", -1)])


async def get_setting(db: Any, key: str, default: Any = None) -> Any:
    doc = await db[SETTINGS].find_one({"key": key})
    return doc["value"] if doc else default


async def set_setting(db: Any, key: str, value: Any) -> None:
    await db[SETTINGS].update_one({"key": key}, {"$set": {"value": value}}, upsert=True)
