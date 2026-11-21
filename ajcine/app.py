from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from . import db as dbm
from .ai import AIIdentifier, OpenRouterClient
from .cache import Cache
from .catalog import Catalog
from .config import Settings
from .corrections import ReviewService
from .delivery import DeliveryService
from .identify import IdentificationPipeline
from .matching import TitleService
from .indexing import AIQueueWorker, Indexer
from .ads import AdService
from .broadcast import BroadcastService, ChatRegistry
from .requests import RequestService
from .watchlist import Watchlist
from .search import SearchEngine
from .tmdb import PosterService
from .users import UserService

log = logging.getLogger("ajcine.app")


@dataclass
class App:
    settings: Settings
    db: Any
    cache: Cache
    catalog: Catalog
    lookup: TitleService
    openrouter: OpenRouterClient
    ai: AIIdentifier
    pipeline: IdentificationPipeline
    review: ReviewService
    search: SearchEngine
    users: UserService
    delivery: DeliveryService
    indexer: Indexer
    ai_worker: AIQueueWorker
    posters: Any = None
    requests: Any = None
    ads: Any = None
    chats: Any = None
    broadcasts: Any = None
    watchlist: Any = None
    tg: Any = None                      # pyrogram bot client (set by the bot runner)
    tg_user: Any = None                 # optional user-session client
    bot_username: str = ""              # set once the bot is connected (used for t.me deep links)
    streams: Any = None                 # StreamLimiter + MediaResolver, built by the web layer
    background: list[asyncio.Task] = field(default_factory=list)
    started_at: float = 0.0

    def client(self) -> Any:
        """Best client for reading source channels (user session when available)."""
        c = self.tg_user or self.tg
        if c is None:
            raise RuntimeError("Telegram client is not running")
        return c

    def spawn(self, coro, name: str) -> asyncio.Task:
        t = asyncio.create_task(coro, name=name)
        self.background.append(t)
        return t

    async def close(self) -> None:
        self.ai_worker.stop()
        await self.indexer.shutdown()
        for t in self.background:
            t.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        if self.posters is not None:
            self.posters.stop()
            await self.posters.tmdb.close()
        await self.openrouter.close()
        await self.lookup.close()
        await self.cache.close()


async def build_app(settings: Settings, db: Any | None = None, cache: Cache | None = None) -> App:
    import time

    if db is None:
        db = await dbm.connect_multi(settings)
    await dbm.ensure_indexes(db)
    if cache is None:
        cache = Cache(settings.redis_url)
        await cache.connect()
    catalog = Catalog(db, cache)
    lookup = TitleService(settings, db, cache)
    orc = OpenRouterClient(settings, db, cache)
    ai = AIIdentifier(orc)
    pipeline = IdentificationPipeline(settings, db, catalog, lookup, ai)
    review = ReviewService(db, catalog, lookup, pipeline)
    search = SearchEngine(db, catalog, cache, ai)
    users = UserService(db, settings.owner_ids)
    delivery = DeliveryService(settings, db, cache, catalog, users)
    app = App(settings=settings, db=db, cache=cache, catalog=catalog, lookup=lookup, openrouter=orc,
              ai=ai, pipeline=pipeline, review=review, search=search, users=users, delivery=delivery,
              indexer=None, ai_worker=None, started_at=time.time())  # type: ignore[arg-type]
    app.indexer = Indexer(settings, db, pipeline, app.client, catalog)
    app.ai_worker = AIQueueWorker(db, pipeline, ai)
    app.posters = PosterService(settings, db, catalog, lookup.tmdb)
    app.requests = RequestService(db, cache, search)
    app.ads = AdService(settings, db, cache)
    app.chats = ChatRegistry(db, cache)
    app.broadcasts = BroadcastService(app)
    app.watchlist = Watchlist(db)
    await app.indexer.refresh_sources()
    return app
