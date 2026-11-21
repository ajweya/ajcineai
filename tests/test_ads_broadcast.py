"""Ads, broadcasts, watchlist and the new bot buttons."""
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ajcine import db as dbm
from ajcine.ads import AdError, clean_ad
from ajcine.broadcast import BroadcastError, clean_message
from ajcine.users import hash_password
from ajcine.web import create_web_app
from tests.conftest import make_msg
from tests.test_bot_flow import CB, Msg, bot, buttons, press, seed  # noqa: F401

H = {"X-Requested-With": "ajcine"}
AD = {"title": "Acme VPN", "link_url": "https://acme.example/vpn", "image_url": "https://img.example/a.jpg",
      "text": "Fast", "placements": ["bot_results", "bot_delivery", "site_banner"]}


class FloodWait(Exception):
    value = 0


class Blocked(Exception):
    pass


Blocked.__name__ = "UserIsBlocked"


class SendBot:
    """Records sends; chat ids in `fail` raise the named error."""
    def __init__(self, fail=None):
        self.sent, self.fail, self.flood_once = [], fail or {}, set()

    async def _go(self, chat_id, kind, payload):
        if chat_id in self.flood_once:
            self.flood_once.discard(chat_id)
            raise FloodWait()
        if chat_id in self.fail:
            raise self.fail[chat_id]()
        self.sent.append((chat_id, kind, payload))

    async def send_message(self, chat_id, text, **k):
        await self._go(chat_id, "text", text)

    async def send_photo(self, chat_id, photo, caption=None, **k):
        await self._go(chat_id, "photo", photo)


async def add_audience(app, users=(1, 2, 3), groups=(-10, -11)):
    for u in users:
        await app.db[dbm.USERS].insert_one({"_id": u, "banned": False})
    for g in groups:
        await app.db[dbm.BOT_CHATS].insert_one({"_id": g, "type": "SUPERGROUP", "active": True})


@pytest.fixture
async def fast(app):
    app.settings = app.settings.with_overrides(broadcast_rate_per_second=30)
    return app


# ---------------------------------------------------------------- ads
def test_ad_validation():
    assert clean_ad(AD)["weight"] == 1
    for bad in ({**AD, "title": "x"}, {**AD, "link_url": "http://insecure.example"}, {**AD, "image_url": "ftp://x.y/z"},
                {**AD, "placements": []}, {**AD, "image_url": "", "placements": ["site_banner"]}, {**AD, "weight": 11},
                {**AD, "starts_at": "2026-02-01", "ends_at": "2026-01-01"}, {**AD, "starts_at": "soon"}):
        with pytest.raises(AdError):
            clean_ad(bad)


async def test_ads_serve_only_live_ads_and_respect_the_global_switch(app):
    a = await app.ads.create(AD, "t")
    old = await app.ads.create({**AD, "title": "Expired", "ends_at": "2020-01-01"}, "t")
    await app.ads.create({**AD, "title": "Future", "starts_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}, "t")
    off = await app.ads.create({**AD, "title": "Off", "active": False}, "t")
    for _ in range(15):
        assert (await app.ads.pick("bot_results"))["_id"] == a["_id"]
    assert await app.ads.pick("nonsense") is None
    await app.ads.set_enabled(False)
    assert await app.ads.pick("bot_results") is None
    await app.ads.set_enabled(True)
    await app.ads.set_active(a["_id"], False)
    assert await app.ads.pick("bot_results") is None
    assert old and off


async def test_ad_counters_and_click_redirect(app):
    a = await app.ads.create(AD, "t")
    await app.ads.impression(a["_id"])
    await app.ads.impression(a["_id"])
    assert await app.ads.click(a["_id"]) == AD["link_url"]
    assert await app.ads.click("not-an-id") is None
    row = (await app.ads.listing())[0]
    assert (row["impressions"], row["clicks"], row["ctr"]) == (2, 1, 50.0)


async def test_bot_results_show_a_sponsored_link_and_no_emoji_buttons(app, bot):
    await seed(app)
    await app.ads.create(AD, "t")
    m = Msg("inception")
    await bot.cb["on_text"](bot, m)
    btns = buttons(m.replies[0].markup)
    assert any(t.startswith("Sponsored: Acme VPN") and d == AD["link_url"] for t, d in btns)
    await app.ads.set_enabled(False)
    m2 = Msg("inception")
    await bot.cb["on_text"](bot, m2)
    assert not any("Sponsored" in t for t, _ in buttons(m2.replies[0].markup))


async def test_delivery_ad_is_sent_once_per_cooldown(app, bot):
    await seed(app)
    await app.ads.create(AD, "t")

    async def deliver():
        m = Msg("inception")
        await bot.cb["on_text"](bot, m)
        r = m.replies[0]
        await press(bot, r, "t:tt1375666")
        await press(bot, r, "l:tt1375666:en")
        await press(bot, r, "q:tt1375666:en:1080p")
        token = [d for t, d in buttons(r.markup) if d.startswith("https://t.me/")][0].split("dl_")[1]
        pm = Msg(uid=42, command=["start", "dl_" + token])
        await bot.cb["on_start"](bot, pm)
        return pm
    first = await deliver()
    assert any("Sponsored" in r.text for r in first.replies)
    second = await deliver()
    assert not any("Sponsored" in r.text for r in second.replies)


def test_every_button_label_is_free_of_emoji():
    from ajcine.bot import admin, runner
    labels = [t for r in runner.home_rows(True) for t, _ in r] + [t for r in admin.menu_rows() for t, _ in r]
    assert all(t.isascii() for t in labels)


# ---------------------------------------------------------------- broadcast
def test_broadcast_message_validation():
    ok = clean_message({"text": "Hi", "audience": "all"})
    assert ok["image_url"] is None
    for bad in ({"text": ""}, {"text": "x", "audience": "nobody"}, {"text": "x" * 1001, "image_url": "https://a.b/c.jpg"},
                {"text": "x", "image_url": "http://a.b/c.jpg"}, {"text": "x", "button_text": "Go"},
                {"text": "x", "button_text": "Go", "button_url": "http://a.b/c"}):
        with pytest.raises(BroadcastError):
            clean_message(bad)


async def test_broadcast_users_groups_and_all(fast):
    app = fast
    await add_audience(app)
    await app.db[dbm.USERS].insert_one({"_id": 9, "banned": True})
    await app.db[dbm.USERS].insert_one({"_id": 8, "blocked": True})
    await app.db[dbm.BOT_CHATS].insert_one({"_id": -12, "active": False})
    for audience, expect in (("users", {1, 2, 3}), ("groups", {-10, -11}), ("all", {1, 2, 3, -10, -11})):
        app.tg = SendBot()
        b = await app.broadcasts.create({"text": "Hello <b>x</b>", "audience": audience}, "t")
        await app.broadcasts.start(b["_id"])
        await app.broadcasts.tasks[b["_id"]]
        assert {c for c, _, _ in app.tg.sent} == expect
        done = await app.broadcasts.get(b["_id"])
        assert done["status"] == "done" and done["stats"]["sent"] == len(expect) and done["total"] == len(expect)
        assert "&lt;b&gt;" in app.tg.sent[0][2]                      # text is escaped, never parsed as markup


async def test_broadcast_marks_unreachable_and_survives_floods(fast):
    app = fast
    await add_audience(app)
    app.tg = SendBot(fail={2: Blocked, -11: Blocked})
    app.tg.flood_once.add(3)
    b = await app.broadcasts.create({"text": "Hi", "audience": "all"}, "t")
    await app.broadcasts.start(b["_id"])
    await app.broadcasts.tasks[b["_id"]]
    done = await app.broadcasts.get(b["_id"])
    assert done["stats"] == {"sent": 3, "failed": 0, "unreachable": 2}
    assert (await app.db[dbm.USERS].find_one({"_id": 2}))["blocked"] is True
    assert (await app.db[dbm.BOT_CHATS].find_one({"_id": -11}))["active"] is False
    counts = await app.chats.counts()
    assert counts == {"users": 2, "groups": 1}                       # unreachable recipients are skipped next time


async def test_broadcast_resumes_from_its_cursor_and_can_be_cancelled(fast):
    app = fast
    await add_audience(app, groups=())
    app.tg = SendBot()
    b = await app.broadcasts.create({"text": "Hi", "audience": "users"}, "t")
    await app.db[dbm.BROADCASTS].update_one({"_id": __import__("bson").ObjectId(b["_id"])}, {"$set": {
        "status": "running", "total": 3, "cursor": {"users": 1, "groups": None}, "stats": {"sent": 1, "failed": 0, "unreachable": 0}}})
    assert await app.broadcasts.resume_all() == 1
    await app.broadcasts.tasks[b["_id"]]
    assert [c for c, _, _ in app.tg.sent] == [2, 3]                  # user 1 is not messaged again
    assert (await app.broadcasts.get(b["_id"]))["stats"]["sent"] == 3
    c = await app.broadcasts.create({"text": "Stop", "audience": "users"}, "t")
    await app.broadcasts.start(c["_id"])
    assert await app.broadcasts.cancel(c["_id"]) is True
    await app.broadcasts.tasks[c["_id"]]
    assert (await app.broadcasts.get(c["_id"]))["status"] == "cancelled"


async def test_broadcast_needs_recipients_and_drafts_only_start_once(fast):
    app = fast
    b = await app.broadcasts.create({"text": "Hi", "audience": "users"}, "t")
    with pytest.raises(BroadcastError):
        await app.broadcasts.start(b["_id"])
    await add_audience(app)
    app.tg = SendBot()
    await app.broadcasts.start(b["_id"])
    await app.broadcasts.tasks[b["_id"]]
    with pytest.raises(BroadcastError):
        await app.broadcasts.start(b["_id"])
    assert await app.broadcasts.delete(b["_id"]) is True


async def test_broadcast_with_image_and_button_and_test_send(fast):
    app = fast
    app.tg = SendBot()
    b = await app.broadcasts.create({"text": "New", "image_url": "https://img.example/p.jpg", "button_text": "Open",
                                     "button_url": "https://x.example/y", "audience": "users"}, "t")
    await app.broadcasts.test(b["_id"], 111)
    assert app.tg.sent == [(111, "photo", "https://img.example/p.jpg")]
    app.tg = SendBot(fail={5: Blocked})
    with pytest.raises(BroadcastError):
        await app.broadcasts.test(b["_id"], 5)


async def test_chat_registry_tracks_groups_only(app):
    mk = lambda i, t: types.SimpleNamespace(id=i, type=types.SimpleNamespace(name=t), title="G", username=None)
    await app.chats.track(mk(-5, "SUPERGROUP"))
    await app.chats.track(mk(77, "PRIVATE"))
    assert await app.db[dbm.BOT_CHATS].count_documents({}) == 1
    await app.chats.left(-5)
    assert (await app.chats.counts())["groups"] == 0
    await app.chats.track(mk(-5, "SUPERGROUP"), force=True)           # re-adding the bot reactivates it at once
    assert (await app.chats.counts())["groups"] == 1


# ---------------------------------------------------------------- watchlist and requests in the bot
async def test_my_list_toggle_view_and_limit(app, bot):
    await seed(app)
    m = Msg("inception")
    await bot.cb["on_text"](bot, m)
    r = m.replies[0]
    await press(bot, r, "t:tt1375666")
    assert ("Add to my list", "w:tt1375666") in buttons(r.markup)
    cb = await press(bot, r, "w:tt1375666")
    assert cb.answers[-1][0] == "Saved to your list."
    assert await app.watchlist.has(42, "tt1375666")
    lm = Msg(command=["mylist"])
    await bot.cb["on_mylist"](bot, lm)
    assert ("Inception (2010)", "t:tt1375666") in buttons(lm.replies[0].markup)
    cb = await press(bot, r, "w:tt1375666")
    assert cb.answers[-1][0] == "Removed from your list." and not await app.watchlist.has(42, "tt1375666")
    empty = Msg(command=["mylist"])
    await bot.cb["on_mylist"](bot, empty)
    assert "empty" in empty.replies[0].text
    for i in range(100):
        assert await app.watchlist.toggle(7, f"tt{1000000 + i}") is True
    assert await app.watchlist.toggle(7, "tt2000000") is None


async def test_request_button_when_nothing_matches(app, bot):
    await seed(app)
    m = Msg("zzqx unknown film")
    await bot.cb["on_text"](bot, m)
    r = m.replies[0]
    rq = [d for t, d in buttons(r.markup) if t == "Request this title"]
    assert rq
    cb = await press(bot, r, rq[0])
    assert "Request sent" in cb.answers[-1][0]
    again = await press(bot, r, rq[0])
    assert "already" in again.answers[-1][0]
    other = await press(bot, r, rq[0], uid=43)
    assert "Added your vote" in other.answers[-1][0]
    assert await app.db[dbm.REQUESTS].count_documents({}) == 1


async def test_help_trending_and_home_menu(app, bot):
    await seed(app)
    h = Msg(command=["help"])
    await bot.cb["on_help"](bot, h)
    assert "/mylist" in h.replies[0].text
    start = Msg(command=["start"])
    await bot.cb["on_start"](bot, start)
    labels = [t for t, _ in buttons(start.replies[0].markup)]
    assert {"My list", "Request a title", "Help"} <= set(labels)
    pm = Msg(command=["recent"])
    await bot.cb["on_listing"](bot, pm)
    assert pm.replies and "Recently added" in pm.replies[0].text


# ---------------------------------------------------------------- web
@pytest.fixture
async def web(app):
    for u, r in (("adm", "admin"), ("mod", "moderator")):
        await app.db[dbm.ADMINS].insert_one({"username": u, "password_hash": hash_password(f"password-{u}12"),
                                             "role": r, "active": True})
    app.public_base_url = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        c.app = app
        yield c


async def login(web, who):
    assert (await web.post("/api/login", json={"username": who, "password": f"password-{who}12"})).status_code == 200


async def test_ads_api_needs_admin_and_validates(web):
    assert (await web.get("/api/ads")).status_code == 401
    await login(web, "mod")
    assert (await web.get("/api/ads")).status_code == 403
    await login(web, "adm")
    r = await web.post("/api/ads", json=AD, headers=H)
    assert r.status_code == 200
    ad_id = r.json()["_id"]
    assert (await web.post("/api/ads", json={**AD, "link_url": "http://x.y/z"}, headers=H)).status_code == 400
    assert (await web.post("/api/ads", json={**AD, "title": ""}, headers=H)).status_code == 422
    assert (await web.put(f"/api/ads/{ad_id}", json={**AD, "title": "Renamed"}, headers=H)).status_code == 200
    assert (await web.put(f"/api/ads/{ad_id}/active", json={"active": False}, headers=H)).status_code == 200
    data = (await web.get("/api/ads")).json()
    assert data["enabled"] is True and data["items"][0]["title"] == "Renamed" and data["items"][0]["active"] is False
    assert (await web.put("/api/ads-switch", json={"enabled": False}, headers=H)).json() == {"enabled": False}
    assert (await web.delete(f"/api/ads/{ad_id}", headers=H)).json() == {"ok": True}


async def test_site_banner_click_tracking_and_switches(web):
    a = await web.app.ads.create(AD, "t")
    banner = (await web.get("/api/public/ads")).json()["ad"]
    assert banner["href"] == f"/go/ad/{a['_id']}" and banner["image"] == AD["image_url"]
    assert "link_url" not in banner
    go = await web.get(banner["href"], follow_redirects=False)
    assert go.status_code == 302 and go.headers["location"] == AD["link_url"]
    row = (await web.app.ads.listing())[0]
    assert (row["impressions"], row["clicks"]) == (1, 1)
    assert (await web.get("/go/ad/zzz", follow_redirects=False)).status_code == 404
    await web.app.ads.set_enabled(False)
    assert (await web.get("/api/public/ads")).json() == {"ad": None}
    assert (await web.get(banner["href"], follow_redirects=False)).status_code == 302   # the link of a shown ad keeps working
    from ajcine.site import set_site_config
    await web.app.ads.set_enabled(True)
    await set_site_config(web.app, public=False)
    assert (await web.get("/api/public/ads")).status_code == 404


async def test_broadcast_api_flow(web):
    app = web.app
    app.settings = app.settings.with_overrides(broadcast_rate_per_second=30)
    app.tg = SendBot()
    await add_audience(app, groups=(-10,))
    await login(web, "mod")
    assert (await web.get("/api/broadcasts")).status_code == 403
    await login(web, "adm")
    assert (await web.get("/api/broadcasts")).json()["audience"] == {"users": 3, "groups": 1}
    assert (await web.post("/api/broadcasts", json={"text": "x", "button_text": "Go"}, headers=H)).status_code == 400
    b = (await web.post("/api/broadcasts", json={"text": "Hello", "audience": "all"}, headers=H)).json()
    assert (await web.post(f"/api/broadcasts/{b['_id']}/test", json={"telegram_id": 111}, headers=H)).json() == {"ok": True}
    assert (await web.post(f"/api/broadcasts/{b['_id']}/start", headers=H)).status_code == 200
    await app.broadcasts.tasks[b["_id"]]
    assert (await web.post(f"/api/broadcasts/{b['_id']}/start", headers=H)).status_code == 400
    assert (await web.get("/api/broadcasts")).json()["items"][0]["stats"]["sent"] == 4
    assert (await web.get("/api/chats")).json()[0]["_id"] == -10
    assert (await web.delete(f"/api/broadcasts/{b['_id']}", headers=H)).json() == {"ok": True}


async def test_database_roles_include_ads(app):
    from ajcine import db as d
    assert set(d.ROLES) >= {"catalog", "files", "users", "activity", "ads", "core"}
    assert d.COLLECTION_ROLE[d.ADS] == "ads" and d.COLLECTION_ROLE[d.BOT_CHATS] == "users"
