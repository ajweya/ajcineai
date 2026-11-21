"""Admin analytics (real counts from history and delivery logs) and the groups list."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ajcine import db as dbm
from ajcine.users import hash_password
from ajcine.web import create_web_app


@pytest.fixture
async def web(app):
    for u, r in (("adm", "admin"), ("view", "viewer")):
        await app.db[dbm.ADMINS].insert_one({"username": u, "password_hash": hash_password(f"password-{u}12"), "role": r, "active": True})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        c.app = app
        yield c


async def login(web, who):
    assert (await web.post("/api/login", json={"username": who, "password": f"password-{who}12"})).status_code == 200


async def test_analytics_counts_are_real_and_windowed(web):
    db, now = web.app.db, datetime.now(timezone.utc)
    h = lambda q, n, days, uid=1: {"user_id": uid, "query": q, "norm_query": q.lower(), "results": n, "ts": now - timedelta(days=days)}
    await db[dbm.SEARCH_HISTORY].insert_many([h("Inception", 3, 0), h("inception", 3, 1, 2), h("Zzz Film", 0, 0), h("Zzz Film", 0, 2), h("Old", 1, 60)])
    d = lambda st, days, tid="tt1375666": {"user_id": 1, "file_id": "f", "title_id": tid, "status": st, "ts": now - timedelta(days=days)}
    await db[dbm.DELIVERIES].insert_many([d("sent", 0), d("sent", 1), d("streamed", 0)])
    await db[dbm.USERS].insert_many([{"_id": 1}, {"_id": 2, "blocked": True}, {"_id": 3, "banned": True}])
    await login(web, "view")
    r = await web.get("/api/analytics?days=7")
    assert r.status_code == 200
    j = r.json()
    assert len(j["labels"]) == 7 and sum(j["searches"]) == 4 and sum(j["deliveries"]) == 2 and sum(j["streams"]) == 1
    assert j["searches"][-1] == 2 and j["active_users"] == 2                      # the 60-day-old search is outside the window
    assert len(j["top_searches"]) == 1 and j["top_searches"][0]["query"].lower() == "inception" and j["top_searches"][0]["count"] == 2
    assert j["no_results"] == [{"query": "Zzz Film", "count": 2}]
    assert j["top_titles"][0]["count"] == 3                                      # deliveries and streams both count as opens
    assert j["users"] == {"total": 3, "blocked": 1, "banned": 1}
    assert len((await web.get("/api/analytics?days=9999")).json()["labels"]) == 90   # the period is capped


async def test_analytics_needs_a_login_and_groups_need_admin(web):
    assert (await web.get("/api/analytics")).status_code == 401
    await web.app.db[dbm.BOT_CHATS].insert_one({"_id": -10, "title": "Movie Fans", "type": "SUPERGROUP", "active": True})
    await login(web, "view")
    assert (await web.get("/api/chats")).status_code == 403
    await login(web, "adm")
    assert (await web.get("/api/chats")).json()[0]["title"] == "Movie Fans"
