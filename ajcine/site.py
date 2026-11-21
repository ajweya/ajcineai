"""Runtime switches for the public website and streaming.

Environment variables give the defaults; administrators can flip them live from the dashboard
(a kill switch for abuse or bandwidth problems) without a restart.
"""
from __future__ import annotations

from typing import Any

from . import db as dbm

KEY = "site_config"


async def site_config(app: Any) -> dict:
    over = await dbm.get_setting(app.db, KEY, {}) or {}
    public = bool(over.get("public", app.settings.public_site_enabled))
    streaming = bool(over.get("streaming", app.settings.streaming_enabled)) and public
    return {"public": public, "streaming": streaming, "base_url": app.settings.public_base_url}


async def set_site_config(app: Any, public: bool | None = None, streaming: bool | None = None) -> dict:
    cur = await dbm.get_setting(app.db, KEY, {}) or {}
    if public is not None:
        cur["public"] = bool(public)
    if streaming is not None:
        cur["streaming"] = bool(streaming)
    await dbm.set_setting(app.db, KEY, cur)
    return await site_config(app)
