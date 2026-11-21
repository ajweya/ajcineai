import asyncio
import types

import httpx
import pytest

from ajcine import db as dbm
from ajcine.catalog import now
from ajcine.delivery import DeliveryError
from ajcine.site import set_site_config
from ajcine.streaming import CHUNK
from ajcine.users import hash_password
from ajcine.web import create_web_app
from tests.conftest import make_msg

SIZE = 2 * CHUNK + 999
DATA = bytes((i * 13) % 251 for i in range(SIZE))
H = {"X-Requested-With": "ajcine"}


class StreamTG:
    def __init__(self, present=True):
        self.present, self.active, self.max_active = present, 0, 0
        self.hold = None

    async def get_messages(self, chat, mid):
        if not self.present:
            class MessageIdInvalid(Exception):
                pass
            raise MessageIdInvalid()
        return make_msg(chat, mid, "x.mkv", size=SIZE)

    async def stream_media(self, message, limit=0, offset=0):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.hold:
                await self.hold.wait()
            for i in range(offset, (SIZE + CHUNK - 1) // CHUNK):
                if limit and i - offset >= limit:
                    return
                yield DATA[i * CHUNK:(i + 1) * CHUNK]
        finally:
            self.active -= 1


async def index(app, name, mid, title_id, season=None, episode=None):
    await app.indexer.ingest_message(make_msg(-100, mid, name, size=SIZE, unique=f"U{mid}"))
    return f"-100:{mid}"


@pytest.fixture
async def site(app):
    app.bot_username = "ajcinebot"
    app.tg = StreamTG()
    await set_site_config(app, True, True)
    await index(app, "Inception.2010.English.1080p.mkv", 1, "tt1375666")
    await index(app, "Inception.2010.Hindi.720p.mp4", 2, "tt1375666")
    await index(app, "Breaking.Bad.S01E01.English.720p.mkv", 3, "tt0903747")
    await index(app, "Breaking.Bad.S01E02.English.720p.mkv", 4, "tt0903747")
    await index(app, "Breaking.Bad.S01.Complete.English.1080p.mkv", 5, "tt0903747")
    api = create_web_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
        c.app = app
        yield c


async def stream_token(site, **body):
    body = {"title_id": "tt1375666", "language": "en", "quality": "1080p", **body}
    r = await site.post("/api/public/stream-link", json=body)
    assert r.status_code == 200, r.text
    return r.json()["watch"].rsplit("/", 1)[1]


# ---- pages and discovery ------------------------------------------------------------------------
async def test_admin_moved_and_site_served(site):
    assert "AJ CINE AI" in (await site.get("/admin")).text
    assert (await site.get("/healthz")).status_code == 200


async def test_home_search_browse_title(site):
    home = (await site.get("/api/public/home")).json()
    ids = {i["id"] for i in home["recent"]}
    assert {"tt1375666", "tt0903747"} <= ids
    assert any(l["code"] == "en" for l in home["languages"])
    s = (await site.get("/api/public/search", params={"q": "inceptoin"})).json()
    assert s["items"] and s["items"][0]["id"] == "tt1375666"
    b = (await site.get("/api/public/browse", params={"kind": "series"})).json()
    assert [i["id"] for i in b["items"]] == ["tt0903747"]
    b = (await site.get("/api/public/browse", params={"language": "hi"})).json()
    assert [i["id"] for i in b["items"]] == ["tt1375666"]
    t = (await site.get("/api/public/title/tt0903747")).json()
    assert t["seasons"][0]["season"] == 1 and t["complete_pack"] is False
    ep = (await site.get("/api/public/title/tt0903747/episodes", params={"season": 1})).json()
    assert [e["episode"] for e in ep["episodes"]] == [1, 2] and ep["has_pack"] is True


async def test_unknown_titles_and_bad_ids_are_404(site):
    assert (await site.get("/api/public/title/tt9999999")).status_code == 404
    assert (await site.get("/api/public/title/not-an-id")).status_code == 404


async def test_options_only_offer_what_exists(site):
    o = (await site.post("/api/public/options", json={"title_id": "tt1375666"})).json()
    assert {l["code"] for l in o["languages"]} == {"en", "hi"} and o["can_stream"] is True
    o = (await site.post("/api/public/options", json={"title_id": "tt1375666", "language": "hi"})).json()
    assert [q["quality"] for q in o["qualities"]] == ["720p"]
    o = (await site.post("/api/public/options", json={"title_id": "tt0903747", "pack": "series"})).json()
    assert o["can_stream"] is False


# ---- no internal identifiers leak ------------------------------------------------------------------
async def test_public_responses_never_expose_telegram_ids(site):
    texts = [
        (await site.get("/api/public/home")).text,
        (await site.get("/api/public/title/tt1375666")).text,
        (await site.post("/api/public/options", json={"title_id": "tt1375666", "language": "en"})).text,
        (await site.get("/api/public/search", params={"q": "inception"})).text,
    ]
    tok = await stream_token(site)
    texts.append((await site.get(f"/api/public/watch/{tok}")).text)
    for t in texts:
        assert "-100" not in t and "source_chat" not in t and "source_message" not in t and "file_id" not in t \
            and "search_tokens" not in t and "FID" not in t


# ---- streaming ---------------------------------------------------------------------------------------
async def test_stream_full_and_ranges(site):
    tok = await stream_token(site)
    r = await site.get(f"/stream/{tok}")
    assert r.status_code == 200 and r.content == DATA and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(SIZE) and r.headers["content-type"].startswith("video/")
    r = await site.get(f"/stream/{tok}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == DATA[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    r = await site.get(f"/stream/{tok}", headers={"Range": f"bytes={CHUNK - 5}-{CHUNK + 5}"})
    assert r.content == DATA[CHUNK - 5:CHUNK + 6]
    r = await site.get(f"/stream/{tok}", headers={"Range": "bytes=-50"})
    assert r.status_code == 206 and r.content == DATA[-50:]
    r = await site.get(f"/stream/{tok}", headers={"Range": f"bytes={SIZE + 5}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{SIZE}"


async def test_stream_head_has_headers_and_no_body(site):
    tok = await stream_token(site)
    r = await site.head(f"/stream/{tok}")
    assert r.status_code == 200 and r.headers["content-length"] == str(SIZE) and r.content == b""
    assert site.app.tg.max_active == 0


async def test_stream_rejects_bad_expired_and_foreign_tokens(site):
    assert (await site.get("/stream/nope")).status_code == 404
    assert (await site.get("/stream/" + "a" * 70)).status_code == 404
    tok = await stream_token(site)
    from datetime import timedelta
    from ajcine.delivery import _h
    await site.app.db[dbm.DELIVERY_TOKENS].update_one({"_id": _h(tok)}, {"$set": {"expires_at": now() - timedelta(seconds=5)}})
    assert (await site.get(f"/stream/{tok}")).status_code in (404, 410)
    # a download token is not a stream token
    dl = await site.app.delivery.create_token(5, ["-100:1"], "tt1375666")
    assert (await site.get(f"/stream/{dl}")).status_code == 404


async def test_stream_token_cannot_be_used_as_download_token(site):
    tok = await stream_token(site)
    with pytest.raises(DeliveryError):
        await site.app.delivery.resolve_token(tok, 5)


async def test_kill_switches(site):
    tok = await stream_token(site)
    await set_site_config(site.app, streaming=False)
    assert (await site.get(f"/stream/{tok}")).status_code == 404
    assert (await site.post("/api/public/stream-link", json={"title_id": "tt1375666", "language": "en"})).status_code == 404
    assert (await site.get("/api/public/config")).json()["streaming"] is False
    assert (await site.get("/api/public/home")).status_code == 200
    await set_site_config(site.app, public=False)
    assert (await site.get("/api/public/home")).status_code == 404
    assert (await site.get("/")).status_code == 404
    await set_site_config(site.app, public=True, streaming=True)
    assert (await site.get(f"/stream/{tok}")).status_code == 200


async def test_streaming_is_off_by_default(app):
    assert app.settings.streaming_enabled is False
    from ajcine.site import site_config
    assert (await site_config(app))["streaming"] is False


async def test_missing_source_returns_404_and_marks_file(site):
    tok = await stream_token(site)
    site.app.tg.present = False
    r = await site.get(f"/stream/{tok}")
    assert r.status_code == 404
    f = await site.app.db[dbm.FILES].find_one({"_id": "-100:1"})
    assert f["missing"] is True
    t = (await site.post("/api/public/options", json={"title_id": "tt1375666", "language": "en"})).json()
    assert t["qualities"] == []


async def test_per_ip_stream_limit_and_release(site):
    tok = await stream_token(site)
    lim = site.app.streams["limiter"]
    for _ in range(lim.max_per_ip):
        lim.acquire("1.2.3.4")
    r = await site.get(f"/stream/{tok}", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429
    assert (await site.get(f"/stream/{tok}", headers={"X-Forwarded-For": "9.9.9.9"})).status_code == 200
    assert lim.total == lim.max_per_ip  # the finished request released its slot


async def test_stream_slot_released_after_client_disconnect(site):
    tok = await stream_token(site)
    lim = site.app.streams["limiter"]
    async with site.stream("GET", f"/stream/{tok}") as r:
        async for _ in r.aiter_bytes(1024):
            break
    await asyncio.sleep(0.05)
    assert lim.total == 0 and site.app.tg.active == 0


async def test_streaming_start_is_logged_separately_from_deliveries(site):
    tok = await stream_token(site)
    await site.get(f"/stream/{tok}")
    assert await site.app.db[dbm.DELIVERIES].count_documents({"status": "streamed"}) == 1
    st = await site.app.catalog.stats()
    assert st["streams"] == 1 and st["deliveries"] == 0


async def test_packs_cannot_be_streamed(site):
    r = await site.post("/api/public/stream-link", json={"title_id": "tt0903747", "language": "en", "pack": "series"})
    assert r.status_code == 400


async def test_stream_link_prefers_browser_friendly_file(site):
    await index(site.app, "Inception.2010.English.1080p.mp4", 9, "tt1375666")
    tok = await stream_token(site)
    w = (await site.get(f"/api/public/watch/{tok}")).json()
    assert w["hint"] == "good" and w["container"] == "mp4" and w["title"] == "Inception"


async def test_watch_info_for_episode(site):
    tok = await stream_token(site, title_id="tt0903747", language="en", quality="720p", season=1, episode=2)
    w = (await site.get(f"/api/public/watch/{tok}")).json()
    assert w["episode"]["season"] == 1 and w["episode"]["episode"] == 2 and w["kind"] == "series"


# ---- website -> Telegram download link ---------------------------------------------------------------
async def test_web_download_link_is_claimed_by_first_telegram_user(site):
    r = await site.post("/api/public/download-link", json={"title_id": "tt1375666", "language": "en", "quality": "1080p"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://t.me/ajcinebot?start=dl_")
    tok = url.split("dl_")[1]
    d = site.app.delivery
    doc = await d.resolve_token(tok, 777)
    assert doc["user_id"] == 777
    with pytest.raises(DeliveryError) as e:
        await d.resolve_token(tok, 778)
    assert e.value.code == "forbidden"
    assert (await d.resolve_token(tok, 777))["user_id"] == 777


async def test_web_download_link_needs_bot_and_existing_version(site):
    r = await site.post("/api/public/download-link", json={"title_id": "tt1375666", "language": "hi", "quality": "2160p"})
    assert r.status_code == 404
    site.app.bot_username = ""
    r = await site.post("/api/public/download-link", json={"title_id": "tt1375666", "language": "en", "quality": "1080p"})
    assert r.status_code == 503


async def test_public_api_is_rate_limited(site):
    codes = [(await site.get("/api/public/config", headers={"X-Forwarded-For": "5.5.5.5"})).status_code for _ in range(250)]
    assert 429 in codes and codes[0] == 200


# ---- security headers and admin separation -----------------------------------------------------------
async def test_security_headers_on_public_and_stream(site):
    tok = await stream_token(site)
    for path in ("/api/public/config", f"/stream/{tok}"):
        r = await site.get(path)
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "no-store" in (await site.get("/api/public/config")).headers["cache-control"]


async def test_public_site_does_not_open_admin_api(site):
    for p in ("/api/stats", "/api/media", "/api/users", "/api/site-config"):
        assert (await site.get(p)).status_code == 401


async def test_admin_can_toggle_site_config(site):
    app = site.app
    await app.db[dbm.ADMINS].insert_one({"username": "own", "password_hash": hash_password("password-own1"),
                                         "role": "owner", "active": True})
    await app.db[dbm.ADMINS].insert_one({"username": "vw", "password_hash": hash_password("password-vw11"),
                                         "role": "viewer", "active": True})
    r = await site.post("/api/login", json={"username": "vw", "password": "password-vw11"})
    assert (await site.get("/api/site-config")).status_code == 200
    assert (await site.put("/api/site-config", json={"streaming": False}, headers=H)).status_code == 403
    await site.post("/api/login", json={"username": "own", "password": "password-own1"})
    r = await site.put("/api/site-config", json={"streaming": False}, headers=H)
    assert r.status_code == 200 and r.json()["streaming"] is False


async def test_login_throttle_ignores_spoofed_forwarded_for_when_proxy_untrusted(app):
    app.settings = app.settings.with_overrides(trust_proxy=False)
    api = create_web_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
        codes = []
        for i in range(25):
            r = await c.post("/api/login", json={"username": f"user{i}", "password": "wrong-password"},
                             headers={"X-Forwarded-For": f"10.0.0.{i}"})
            codes.append(r.status_code)
        assert 429 in codes


async def test_site_pages_and_assets_are_served_without_inline_code(site):
    for path in ("/", "/search", "/browse", "/title/tt1375666", "/watch/abcdefgh1234"):
        r = await site.get(path)
        assert r.status_code == 200 and "AJ CINE AI" in r.text, path
        assert "<script>" not in r.text and " style=" not in r.text and "onclick=" not in r.text
        assert "Developed by AJ APPLICATIONS · Developer: AJWEYA" in r.text and "TMDB" in r.text
    assert "text/css" in (await site.get("/assets/css/theme.css")).headers["content-type"]
    for page in ("/movies", "/series", "/anime", "/library", "/settings"):
        assert (await site.get(page)).status_code == 200, page
    assert "innerHTML" in (await site.get("/assets/js/site.js")).text
    assert (await site.get("/assets/../../etc/passwd")).status_code in (404, 400)


# ---- player data and theme assets -------------------------------------------------------------------
async def test_watch_info_offers_quality_switch_and_next_episode(site):
    tok = await stream_token(site)
    assert (await site.get(f"/api/public/watch/{tok}")).json()["qualities"] == []       # one version: nothing to switch
    await index(site.app, "Inception.2010.English.720p.mp4", 11, "tt1375666")
    w = (await site.get(f"/api/public/watch/{await stream_token(site)}")).json()
    assert {q["quality"] for q in w["qualities"]} == {"1080p", "720p"}
    assert [q["current"] for q in w["qualities"]].count(True) == 1
    cur = next(q for q in w["qualities"] if q["current"])
    assert cur["quality"] == w["quality"]
    other = next(q for q in w["qualities"] if not q["current"])
    assert (await site.post("/api/public/stream-link", json=other["select"])).status_code == 200

    ep1 = (await site.get(f"/api/public/watch/{await stream_token(site, title_id='tt0903747', quality='720p', season=1, episode=1)}")).json()
    assert ep1["next"]["season"] == 1 and ep1["next"]["episode"] == 2
    assert (await site.post("/api/public/stream-link", json=ep1["next"]["select"])).status_code == 200
    ep2 = (await site.get(f"/api/public/watch/{await stream_token(site, title_id='tt0903747', quality='720p', season=1, episode=2)}")).json()
    assert ep2["next"] is None
    assert (await site.get(f"/api/public/watch/{await stream_token(site)}")).json()["next"] is None


async def test_dependencies_are_pinned_checked_and_bundled(site):
    import base64, hashlib, re
    from pathlib import Path
    root = Path(__file__).parent.parent / "ajcine" / "web" / "site"
    deps = (root / "js" / "deps.js").read_text()
    html = (await site.get("/")).text
    entries = re.findall(r'\{ type: "(?:css|js)", url: `\$\{CDN\}/([^`]+)`, sri: "sha384-([^"]+)", local: (null|"[^"]+")', deps)
    assert len(entries) == 8
    for path, sri, local in entries:
        assert re.search(r"@\d+\.\d+\.\d+/", path), f"{path} is not version-pinned"
        if local != "null":
            data = (root / local.strip('"').replace("/assets/", "")).read_bytes()
            assert base64.b64encode(hashlib.sha384(data).digest()).decode() == sri, f"bundled copy differs from the pinned CDN file: {path}"
    for m in re.finditer(r'<link data-dep="[^"]+" rel="stylesheet" href="https://cdn\.jsdelivr\.net/npm/([^"]+)" integrity="sha384-([^"]+)" crossorigin="anonymous"', html):
        assert (m.group(1), m.group(2)) in {(p, s) for p, s, _ in entries}, "index.html and deps.js disagree"
    assert 'data-bs-theme="dark"' in html and html.count("<script") == 1 and 'type="module"' in html
    assert 'family=Inter' in html


async def test_public_csp_allows_only_pinned_cdns_and_admin_stays_strict(site):
    pub = (await site.get("/")).headers["content-security-policy"]
    assert "script-src 'self' https://cdn.jsdelivr.net;" in pub and "https://fonts.googleapis.com" in pub and "https://fonts.gstatic.com" in pub
    assert "unsafe-inline" not in pub and "unsafe-eval" not in pub
    adm = (await site.get("/admin")).headers["content-security-policy"]
    assert "jsdelivr" not in adm and "googleapis" not in adm and "script-src 'self';" in adm
    assert "jsdelivr" in (await site.get("/assets/js/site.js")).headers["content-security-policy"]
    assert "AJPlayer" not in (await site.get("/assets/js/player/aj-player.js")).text and "AJ CINE AI PLAYER" in (await site.get("/assets/js/player/aj-player.js")).text


async def test_browse_filters_by_year_and_lists_years(site):
    r = (await site.get("/api/public/browse")).json()
    assert r["years"] and all(isinstance(y, int) for y in r["years"])
    y = r["years"][0]
    only = (await site.get(f"/api/public/browse?year={y}")).json()
    assert only["items"] and {i["year"] for i in only["items"]} == {y}
    assert (await site.get("/api/public/browse?year=1900")).json()["items"] == []
    assert "trending" in (await site.get("/api/public/home")).json()


async def test_watch_info_has_previous_episode_and_no_internal_ids(site):
    ep2 = (await site.get(f"/api/public/watch/{await stream_token(site, title_id='tt0903747', quality='720p', season=1, episode=2)}")).json()
    assert ep2["prev"]["episode"] == 1 and ep2["prev"]["select"]["season"] == 1
    assert "file_id" not in ep2
