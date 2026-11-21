"""Drives the real Telegram handlers with fake Pyrogram objects."""
import types
import pytest
from ajcine import db as dbm
from ajcine.bot import register_handlers
from tests.conftest import make_msg


class FakeBot:
    def __init__(self):
        self.handlers = []
        self.sent = []

    def add_handler(self, h, group=0):
        self.handlers.append(h)

    async def get_me(self):
        return types.SimpleNamespace(username="AJCineBot")

    async def copy_message(self, to, from_chat, mid, caption=None, protect_content=False):
        self.sent.append((to, from_chat, mid))
        return types.SimpleNamespace(id=1)

    async def get_chat_member(self, *a):
        return types.SimpleNamespace(status="member", is_member=True)


class Msg:
    def __init__(self, text=None, uid=42, command=None, private=True):
        self.text, self.command = text, command or []
        self.from_user = types.SimpleNamespace(id=uid, first_name="U", username="u")
        self.chat = types.SimpleNamespace(type=types.SimpleNamespace(name="PRIVATE" if private else "SUPERGROUP"), id=uid)
        self.replies = []
        self.photo = None

    async def reply_text(self, text, reply_markup=None, quote=False, **k):
        r = Msg(text)
        r.markup = reply_markup
        self.replies.append(r)
        return r

    async def edit_text(self, text, reply_markup=None, **k):
        self.text, self.markup = text, reply_markup

    async def delete(self):
        pass


class CB:
    def __init__(self, data, message, uid=42):
        self.data, self.message = data, message
        self.from_user = types.SimpleNamespace(id=uid, first_name="U", username="u")
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def buttons(markup):
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


class RecordingHandler:
    """Stands in for Pyrogram's handler classes so the original callbacks stay reachable."""
    def __init__(self, callback, filters=None):
        self.callback, self.filters = callback, filters


@pytest.fixture
async def bot(app, monkeypatch):
    import pyrogram.handlers as ph
    for name in ("MessageHandler", "CallbackQueryHandler", "DeletedMessagesHandler"):
        monkeypatch.setattr(ph, name, RecordingHandler)
    b = FakeBot()
    app.tg = b
    register_handlers(app, b, None)
    b.cb = {h.callback.__name__: h.callback for h in b.handlers}
    return b


async def seed(app):
    names = ["Inception.2010.English.480p.mkv", "Inception.2010.English.1080p.mkv", "Inception (2010) Malayalam 720p.mkv",
             "Breaking.Bad.S01E01.English.720p.mkv", "Breaking.Bad.S01E01.English.1080p.mkv",
             "Breaking.Bad.S01E02.English.720p.mkv", "Breaking.Bad.S01.Complete.English.720p.mkv"]
    for i, n in enumerate(names, 1):
        await app.indexer.ingest_message(make_msg(-100, i, n, unique=f"U{i}"))


async def press(bot, msg, data, uid=42):
    cb = CB(data, msg, uid)
    handler = bot.cb["on_callback"] if not data.startswith(("h:", "a:")) else (bot.cb["on_home"] if data.startswith("h:") else bot.cb["on_admin"])
    await handler(bot, cb)
    return cb


async def test_movie_flow_search_language_quality_private_delivery(app, bot):
    await seed(app)
    m = Msg("inceptoin")                                  # typo on purpose
    await bot.cb["on_text"](bot, m)
    results = m.replies[0]
    assert "Results for" in results.text
    btns = buttons(results.markup)
    assert btns[0][1] == "t:tt1375666"

    await press(bot, results, "t:tt1375666")
    langs = buttons(results.markup)
    assert {t.split(" (")[0] for t, d in langs if d.startswith("l:")} == {"English", "Malayalam"}     # only real languages
    assert "Choose a language" in results.text

    await press(bot, results, "l:tt1375666:en")
    quals = [t for t, d in buttons(results.markup) if d.startswith("q:")]
    assert len(quals) == 2 and any("1080p" in q for q in quals) and not any("720p" in q for q in quals)

    cb = await press(bot, results, "q:tt1375666:en:1080p")
    url = [d for t, d in buttons(results.markup) if d.startswith("https://t.me/")][0]
    assert url.startswith("https://t.me/AJCineBot?start=dl_")
    token = url.split("dl_")[1]

    # open the private chat: /start dl_<token>
    pm = Msg(uid=42, command=["start", "dl_" + token])
    await bot.cb["on_start"](bot, pm)
    assert bot.sent and bot.sent[-1] == (42, -100, 2)                 # the 1080p English file: message id 2
    assert any("Sent 1 file" in r.text for r in pm.replies)

    # someone else opening the same link gets nothing
    bot.sent.clear()
    other = Msg(uid=43, command=["start", "dl_" + token])
    await bot.cb["on_start"](bot, other)
    assert not bot.sent and any("another user" in r.text for r in other.replies)


async def test_series_flow_season_episode_language_quality(app, bot):
    await seed(app)
    m = Msg("breaking bad")
    await bot.cb["on_text"](bot, m)
    res = m.replies[0]
    await press(bot, res, "t:tt0903747")
    b = buttons(res.markup)
    assert ("Season 1 (2)", "n:tt0903747:1") in b
    assert not [x for x in b if x[1].startswith("cp:")]               # no complete-series pack indexed
    await press(bot, res, "n:tt0903747:1")
    b = buttons(res.markup)
    assert ("E01", "e:tt0903747:1:1") in b and ("E02", "e:tt0903747:1:2") in b
    assert any(d == "sp:tt0903747:1" for _, d in b)                   # season pack offered
    await press(bot, res, "e:tt0903747:1:1")
    assert [d for _, d in buttons(res.markup) if d.startswith("el:")] == ["el:tt0903747:1:1:en"]
    await press(bot, res, "el:tt0903747:1:1:en")
    qs = [d for _, d in buttons(res.markup) if d.startswith("eq:")]
    assert sorted(qs) == ["eq:tt0903747:1:1:en:1080p", "eq:tt0903747:1:1:en:720p"]
    await press(bot, res, "eq:tt0903747:1:1:en:720p")
    assert any(d.startswith("https://t.me/AJCineBot?start=dl_") for _, d in buttons(res.markup))
    # season pack path
    await press(bot, res, "sp:tt0903747:1")
    await press(bot, res, "pl:tt0903747:s:1:en")
    await press(bot, res, "pq:tt0903747:s:1:en:720p")
    assert any(d.startswith("https://t.me/") for _, d in buttons(res.markup))


async def test_navigation_edits_message_instead_of_sending_new_ones(app, bot):
    await seed(app)
    m = Msg("inception")
    await bot.cb["on_text"](bot, m)
    res = m.replies[0]
    before = len(m.replies)
    await press(bot, res, "t:tt1375666")
    await press(bot, res, "l:tt1375666:en")
    assert len(m.replies) == before and len(res.replies) == 0


async def test_invalid_and_stale_callbacks_are_safe(app, bot):
    m = Msg("x")
    cb = await press(bot, m, "t:tt9999999")
    assert cb.answers and cb.answers[0][1] is True                    # "no longer available" alert
    cb2 = CB("q:tt1375666:en:1080p;DROP", m)
    await bot.cb["on_callback"](bot, cb2)
    assert cb2.answers[0][0].startswith("This button is no longer valid")


async def test_banned_user_gets_nothing(app, bot):
    await seed(app)
    await app.users.touch(types.SimpleNamespace(id=42, first_name="a", username="a"))
    await app.users.ban(42)
    m = Msg("inception")
    await bot.cb["on_text"](bot, m)
    assert "disabled" in m.replies[0].text and "Results" not in m.replies[0].text


async def test_admin_panel_requires_admin_role(app, bot):
    m = Msg("menu")
    denied = await press(bot, m, "a:home", uid=42)
    assert denied.answers[0] == ("Not authorized.", True)
    ok = await press(bot, m, "a:home", uid=111)                       # owner from OWNER_IDS
    assert "Admin" in m.text and any(d == "a:dash" for _, d in buttons(m.markup))
    await press(bot, m, "a:dash", uid=111)
    assert "Dashboard" in m.text and "Files" in m.text


async def test_moderator_cannot_reach_indexing_or_users_in_telegram(app, bot):
    await app.users.grant_telegram_admin(77, "moderator")
    m = Msg("menu")
    for data in ("a:idx", "a:src", "a:us", "a:ai", "a:im", "a:se"):
        cb = await press(bot, m, data, uid=77)
        assert cb.answers[0] == ("You do not have permission for this.", True), data
    await press(bot, m, "a:rv", uid=77)
    assert "Review" in m.text or "empty" in m.text


async def test_admin_text_prompt_is_ignored_for_non_admins(app, bot):
    from ajcine.bot import admin
    await admin._state_set(app, 42, a="srcadd")            # even if a stale state exists
    m = Msg("@somechannel")
    assert await admin.handle_text_input(app, bot, m) is False
    assert await app.cache.get("adm_state:42") is None


async def test_review_approval_from_telegram(app, bot):
    app.pipeline.ai = None
    await app.indexer.ingest_message(make_msg(-100, 1, "Weird.Movie.Name.2014.1080p.mkv", unique="W"))
    m = Msg("menu")
    await press(bot, m, "a:rv", uid=111)
    assert "Review" in m.text
    cand_btn = [d for _, d in buttons(m.markup) if d.startswith("a:rvc:0:")]
    assert cand_btn
    await press(bot, m, cand_btn[0], uid=111)
    f = await app.db[dbm.FILES].find_one({"_id": "-100:1"})
    assert f["status"] == "identified"
    hist = await app.review.history()
    assert hist[0]["actor"] == {"type": "telegram", "id": 111}


async def test_owner_can_switch_website_and_streaming_from_telegram(app, bot):
    from ajcine.site import site_config
    m = Msg("menu")
    await press(bot, m, "a:web", uid=111)
    assert "Website" in m.text and ("a:web:p" in [d for _, d in buttons(m.markup)])
    assert (await site_config(app))["public"] is True and (await site_config(app))["streaming"] is False
    await press(bot, m, "a:web:s", uid=111)
    assert (await site_config(app))["streaming"] is True and "Streaming: ✅ on" in m.text
    await press(bot, m, "a:web:p", uid=111)
    cfg = await site_config(app)
    assert cfg["public"] is False and cfg["streaming"] is False     # streaming cannot run without the website
    assert "Public website: ⛔ off" in m.text


async def test_moderator_cannot_switch_website_in_telegram(app, bot):
    from ajcine.site import site_config
    await app.users.grant_telegram_admin(77, "moderator")
    cb = await press(bot, Msg("menu"), "a:web:p", uid=77)
    assert cb.answers[0] == ("You do not have permission for this.", True)
    assert (await site_config(app))["public"] is True


def test_no_button_label_contains_an_emoji(app):
    from ajcine.bot import admin, ui
    import re
    emoji = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF\uFE0F]")
    rows = admin.menu_rows() + ui.language_keyboard("tt1", [{"code": "en", "name": "English", "files": 2}], "l:tt1:{lang}", "t:tt1")
    markup = ui.to_markup(rows + [[("🔥 Hot", "x:1"), ("▶️ Go", "url:https://a.example/x")]])
    for row in markup.inline_keyboard:
        for b in row:
            assert not emoji.search(b.text) and b.text.strip(), b.text
