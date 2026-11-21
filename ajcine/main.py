from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import brand
from .app import build_app
from .config import Settings
from .logging_setup import setup_logging

log = logging.getLogger("ajcine.main")


async def run() -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    log.info("starting %s v%s — %s · %s", brand.PRODUCT, brand.VERSION, brand.CREDIT_LINE, brand.DEVELOPER_LINE)
    problems = settings.problems()
    if problems:
        for p in problems:
            log.error("configuration: %s", p)
        return 2

    app = await build_app(settings)

    import uvicorn
    from .bot import create_clients, register_handlers
    from .web import create_web_app

    bot, user_client = create_clients(settings)
    app.tg, app.tg_user = bot, user_client
    register_handlers(app, bot, user_client)

    web = create_web_app(app)
    server = uvicorn.Server(uvicorn.Config(web, host=settings.web_host, port=settings.web_port,
                                           log_level="warning", access_log=False, proxy_headers=True,
                                           forwarded_allow_ips="*"))
    server.install_signal_handlers = lambda: None  # we own signal handling

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    web_task = asyncio.create_task(server.serve(), name="web")  # health check is available while Telegram starts
    try:
        await bot.start()
        if user_client is not None:
            await user_client.start()
        me = await bot.get_me()
        app.bot_username = me.username or ""
        log.info("bot online as @%s", me.username)

        resumed = await app.indexer.resume()
        app.spawn(app.indexer.supervisor(), "indexer-supervisor")
        app.spawn(app.ai_worker.run(), "ai-queue-worker")
        app.spawn(app.posters.run(), "tmdb-posters")
        await app.broadcasts.resume_all()
        log.info("ready (resumed %d job(s)); dashboard on :%d", resumed, settings.web_port)

        await stop.wait()
    finally:
        log.info("shutting down…")
        server.should_exit = True
        await app.close()           # checkpoints are already persisted per batch
        for c in (user_client, bot):
            try:
                if c is not None and c.is_connected:
                    await c.stop()
            except Exception:
                pass
        try:
            await asyncio.wait_for(web_task, timeout=10)
        except Exception:
            web_task.cancel()
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
