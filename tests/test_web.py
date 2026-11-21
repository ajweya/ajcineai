import os
import httpx
import pytest
from ajcine import db as dbm
from ajcine.catalog import now
from ajcine.users import hash_password
from ajcine.web import create_web_app
from tests.conftest import make_msg

H = {"X-Requested-With": "ajcine"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
async def web(app):
    for name, role in (("owner1", "owner"), ("admin1", "admin"), ("mod1", "moderator"), ("view1", "viewer")):
        await app.db[dbm.ADMINS].insert_one({"username": name, "password_hash": hash_password("password-" + name),
                                             "role": role, "active": True})
    api = create_web_app(app)
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def login(web, who):
    r = await web.post("/api/login", json={"username": who, "password": "password-" + who})
    assert r.status_code == 200, r.text
    return r


async def test_healthz_is_public(web):
    r = await web.get("/healthz")
    assert r.status_code == 200 and r.json()["checks"]["mongo"] is True


async def test_api_requires_authentication(web):
    for path in ("/api/stats", "/api/media", "/api/review", "/api/users", "/api/sources", "/api/ai/status", "/api/logs"):
        assert (await web.get(path)).status_code == 401


async def test_login_failures_and_cookie_flags(web):
    assert (await web.post("/api/login", json={"username": "owner1", "password": "wrong-password"})).status_code == 401
    assert (await web.post("/api/login", json={"username": "ghost", "password": "whatever-pass"})).status_code == 401
    r = await login(web, "owner1")
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "samesite=strict" in c
    assert "password_hash" not in r.text


async def test_login_is_rate_limited(web):
    codes = [(await web.post("/api/login", json={"username": "owner1", "password": "bad-password-x"})).status_code for _ in range(14)]
    assert 429 in codes
    assert (await web.post("/api/login", json={"username": "owner1", "password": "password-owner1"})).status_code == 429


async def test_csrf_header_required_for_mutations(web):
    await login(web, "admin1")
    assert (await web.post("/api/cache/clear")).status_code == 403
    assert (await web.post("/api/cache/clear", headers=H)).status_code == 200


async def test_role_based_permissions(web, app):
    await login(web, "view1")
    assert (await web.get("/api/stats")).status_code == 200
    assert (await web.get("/api/review")).status_code == 403          # needs moderator
    assert (await web.get("/api/users")).status_code == 403           # needs admin
    assert (await web.post("/api/cache/clear", headers=H)).status_code == 403
    await web.post("/api/logout", headers=H)
    await login(web, "mod1")
    assert (await web.get("/api/review")).status_code == 200
    assert (await web.get("/api/users")).status_code == 403
    assert (await web.post("/api/jobs", headers=H, json={"chat_id": 1})).status_code == 403
    await web.post("/api/logout", headers=H)
    await login(web, "admin1")
    assert (await web.get("/api/users")).status_code == 200
    assert (await web.get("/api/backups")).status_code == 403         # owner only
    assert (await web.get("/api/admins")).status_code == 403
    await web.post("/api/logout", headers=H)
    await login(web, "owner1")
    assert (await web.get("/api/backups")).status_code == 200 and (await web.get("/api/admins")).status_code == 200


async def test_logout_invalidates_session(web):
    await login(web, "owner1")
    assert (await web.get("/api/me")).status_code == 200
    await web.post("/api/logout", headers=H)
    assert (await web.get("/api/me")).status_code == 401


async def test_disabled_admin_loses_access_immediately(web, app):
    await login(web, "admin1")
    await app.db[dbm.ADMINS].update_one({"username": "admin1"}, {"$set": {"active": False}})
    assert (await web.get("/api/stats")).status_code == 401


async def test_dashboard_statistics_reflect_real_records(web, app):
    for i, n in enumerate(["Inception.2010.English.1080p.mkv", "Inception.2010.Hindi.720p.mkv", "Breaking.Bad.S01E01.720p.mkv",
                           "Breaking.Bad.S01E02.720p.mkv", "zzz.unknown.mkv"], 1):
        await app.indexer.ingest_message(make_msg(-100, i, n, unique=f"U{i}"))
    await app.db[dbm.SEARCH_HISTORY].insert_one({"user_id": 1, "query": "x", "norm_query": "x", "results": 0, "ts": now()})
    await login(web, "owner1")
    s = (await web.get("/api/stats")).json()
    assert (s["files"], s["movies"], s["series"], s["seasons"], s["episodes"]) == (5, 1, 1, 1, 2)
    assert s["files_identified"] == 4 and s["searches"] == 1 and s["deliveries"] == 0
    assert s["review_queue"] + s["files_unidentified"] >= 1


async def test_review_approve_flow_and_history(web, app):
    app.pipeline.ai = None
    await app.indexer.ingest_message(make_msg(-100, 1, "Weird.Movie.Name.2014.1080p.mkv", unique="W1"))
    await login(web, "mod1")
    q = (await web.get("/api/review")).json()
    assert q["total"] == 1
    fid = q["items"][0]["file_id"]
    item = (await web.get(f"/api/review/{fid}")).json()
    assert item["file"]["file_name"].startswith("Weird")
    bad = await web.post(f"/api/review/{fid}/approve", headers=H, json={"imdb_id": "not-an-id"})
    assert bad.status_code == 422
    ok = await web.post(f"/api/review/{fid}/approve", headers=H, json={"imdb_id": "tt0816692"})
    assert ok.status_code == 200 and ok.json()["approved"] == 1
    assert (await web.get("/api/review")).json()["total"] == 0
    hist = (await web.get("/api/corrections")).json()
    assert hist and hist[0]["actor"]["username"] == "mod1" and hist[0]["imdb_id"] == "tt0816692"
    media = (await web.get("/api/media/tt0816692")).json()
    assert media["files"][0]["status"] == "identified"


async def test_edit_title_and_poster_upload_validation(web, app):
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.English.1080p.mkv", unique="I1"))
    await login(web, "mod1")
    r = await web.patch("/api/media/tt1375666", headers=H, json={"title": "Inception (Edited)", "genres": ["Sci-Fi"]})
    assert r.status_code == 200
    assert (await app.catalog.get_title("tt1375666"))["title"] == "Inception (Edited)"
    assert (await web.patch("/api/media/tt1375666", headers=H, json={"year": 1500})).status_code == 422
    P = "/api/media/tt1375666/poster"
    for bad in ("http://insecure.example/p.jpg", "javascript:alert(1)", "https://x", "ftp://a.example/p.png", "https://a.example/p p.png"):
        assert (await web.put(P, headers=H, json={"url": bad})).status_code in (400, 422), bad
    assert (await web.post(P, headers=H, files={"file": ("p.png", PNG)})).status_code == 405  # no file uploads
    ok = await web.put(P, headers=H, json={"url": "https://image.tmdb.org/t/p/w500/abc.jpg"})
    assert ok.status_code == 200
    t = await app.catalog.get_title("tt1375666")
    assert t["poster"] == "https://image.tmdb.org/t/p/w500/abc.jpg" and t["poster_source"] == "manual"
    assert not os.path.exists(os.path.join(app.settings.poster_dir, "tt1375666.png"))
    assert (await web.delete(P, headers=H)).status_code == 200
    assert (await app.catalog.get_title("tt1375666")).get("poster") is None
    assert (await web.get("/posters/..%2f..%2fetc%2fpasswd")).status_code == 404
    assert (await web.get("/posters/tt1375666.svg")).status_code == 404


async def test_user_ban_unban_and_owner_protection(web, app):
    await app.db[dbm.USERS].insert_one({"_id": 500, "username": "bob", "banned": False})
    await login(web, "admin1")
    assert (await web.post("/api/users/500/ban", headers=H, json={"reason": "spam"})).status_code == 200
    assert await app.users.is_banned(500)
    assert (await web.post("/api/users/111/ban", headers=H, json={})).status_code == 400
    assert (await web.post("/api/users/500/unban", headers=H)).status_code == 200 and not await app.users.is_banned(500)


async def test_ai_config_rejects_paid_models_via_api(web, app):
    await login(web, "admin1")
    app.openrouter._catalog = {"vendor/model-a:free": {"pricing": {"prompt": "0", "completion": "0"}},
                               "vendor/paid": {"pricing": {"prompt": "0.1", "completion": "0.1"}}}
    app.openrouter._catalog_ok, app.openrouter._catalog_at = True, 1e18
    assert (await web.post("/api/ai/config", headers=H, json={"model": "vendor/paid"})).status_code == 400
    assert (await web.post("/api/ai/config", headers=H, json={"model": "vendor/model-a:free"})).status_code == 200
    s = (await web.get("/api/ai/status")).json()
    assert "sk-test" not in str(s) and s["key_configured"] is True


async def test_no_secrets_in_any_response(web, app):
    await login(web, "owner1")
    for path in ("/api/me", "/api/system", "/api/stats", "/api/ai/status", "/api/admins", "/api/logs"):
        body = (await web.get(path)).text
        assert "sk-test" not in body and "scrypt$" not in body and "k" * 40 not in body


async def test_security_headers_and_dashboard_served(web):
    r = await web.get("/admin")
    assert r.status_code == 200 and "AJ CINE AI" in r.text and "Developed by AJ APPLICATIONS" in r.text
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert (await web.get("/static/app.js")).status_code == 200


async def test_backup_roundtrip(web, app, tmp_path):
    from ajcine.tools.backup import create_backup, restore_backup
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.English.1080p.mkv", unique="I1"))
    info = await create_backup(app.db, str(tmp_path))
    await app.db[dbm.FILES].delete_many({})
    await app.db[dbm.MOVIES].delete_many({})
    counts = await restore_backup(app.db, info["path"])
    assert counts[dbm.FILES] == 1 and counts[dbm.MOVIES] == 1
    assert (await app.db[dbm.FILES].find_one({"_id": "-100:1"}))["title_id"] == "tt1375666"
    assert dbm.WEB_SESSIONS not in info["counts"] and dbm.DELIVERY_TOKENS not in info["counts"]


async def test_first_run_bootstrap_creates_owner_once(app):
    api = create_web_app(app)
    async with api.router.lifespan_context(api):
        pass
    owner = await app.db[dbm.ADMINS].find_one({"username": "admin"})
    assert owner["role"] == "owner" and owner["password_hash"].startswith("scrypt$")
    assert "correct-horse-battery" not in str(owner)
    async with api.router.lifespan_context(api):       # restart: must not duplicate or reset
        pass
    assert await app.db[dbm.ADMINS].count_documents({"username": "admin"}) == 1
