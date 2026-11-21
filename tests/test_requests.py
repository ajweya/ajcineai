import httpx
import pytest

from ajcine import db as dbm
from ajcine.requests import RequestError
from ajcine.users import hash_password
from ajcine.web import create_web_app
from tests.conftest import make_msg

H = {"X-Requested-With": "ajcine"}


@pytest.fixture
async def web(app):
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.English.1080p.mkv", unique="U1"))
    for u, r in (("mod", "moderator"), ("view", "viewer")):
        await app.db[dbm.ADMINS].insert_one({"username": u, "password_hash": hash_password(f"password-{u}12"),
                                             "role": r, "active": True})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        c.app = app
        yield c


async def test_request_for_missing_title_is_created_and_deduplicated(web):
    r = await web.post("/api/public/requests", json={"title": "Kuttavum Shikshayum", "year": 2015, "kind": "movie"},
                       headers={"X-Forwarded-For": "1.1.1.1"})
    assert r.json() == {"status": "created", "votes": 1, "request_status": "open"}
    again = await web.post("/api/public/requests", json={"title": "kuttavum  shikshayum", "year": 2015, "kind": "movie"},
                           headers={"X-Forwarded-For": "1.1.1.1"})
    assert again.json()["status"] == "already"
    other = await web.post("/api/public/requests", json={"title": "Kuttavum Shikshayum", "year": 2015, "kind": "movie"},
                           headers={"X-Forwarded-For": "2.2.2.2"})
    assert other.json()["status"] == "voted" and other.json()["votes"] == 2
    assert await web.app.db[dbm.REQUESTS].count_documents({}) == 1


async def test_request_for_title_already_in_library_points_to_it(web):
    r = await web.post("/api/public/requests", json={"title": "Inception", "year": 2010})
    assert r.json()["status"] == "exists" and r.json()["title_id"] == "tt1375666"
    assert await web.app.db[dbm.REQUESTS].count_documents({}) == 0


async def test_request_validation_and_rate_limit(web):
    for bad in ({"title": "x"}, {"title": "Fine Title", "year": 1500}, {"title": "Fine Title", "kind": "game"},
                {"title": "A" * 200}):
        assert (await web.post("/api/public/requests", json=bad)).status_code == 422
    codes = [(await web.post("/api/public/requests", json={"title": f"Unknown Film {i}"},
                             headers={"X-Forwarded-For": "9.9.9.9"})).status_code for i in range(8)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


async def test_requests_off_when_site_is_off(web):
    from ajcine.site import set_site_config
    await set_site_config(web.app, public=False)
    assert (await web.post("/api/public/requests", json={"title": "Some Film"})).status_code == 404


async def test_voter_identity_is_stored_only_as_hash(web):
    await web.post("/api/public/requests", json={"title": "Hidden Film"}, headers={"X-Forwarded-For": "7.7.7.7"})
    doc = await web.app.db[dbm.REQUESTS].find_one({})
    assert "7.7.7.7" not in str(doc)


async def test_admin_lists_updates_and_sees_when_title_arrives(web):
    await web.post("/api/public/requests", json={"title": "Breaking Bad", "kind": "series"}, headers={"X-Forwarded-For": "3.3.3.3"})
    await web.post("/api/public/requests", json={"title": "Zzz Unknown Film"}, headers={"X-Forwarded-For": "3.3.3.3"})
    assert (await web.get("/api/requests")).status_code == 401
    await web.post("/api/login", json={"username": "view", "password": "password-view12"})
    lst = (await web.get("/api/requests")).json()
    assert {r["title"] for r in lst} == {"Breaking Bad", "Zzz Unknown Film"} and all("voters" not in r for r in lst)
    rid = lst[0]["_id"]
    assert (await web.put(f"/api/requests/{rid}", json={"status": "done"}, headers=H)).status_code == 403
    await web.post("/api/login", json={"username": "mod", "password": "password-mod12"})
    await web.app.indexer.ingest_message(make_msg(-100, 2, "Breaking.Bad.S01E01.English.720p.mkv", unique="U2"))
    lst = (await web.get("/api/requests")).json()
    bb = next(r for r in lst if r["title"] == "Breaking Bad")
    assert bb["in_library"]["id"] == "tt0903747"
    assert (await web.put(f"/api/requests/{bb['_id']}", json={"status": "done"}, headers=H)).status_code == 200
    assert (await web.put(f"/api/requests/{bb['_id']}", json={"status": "bogus"}, headers=H)).status_code == 422
    assert (await web.put("/api/requests/notanid", json={"status": "done"}, headers=H)).status_code in (400, 404, 422)
    assert [r["title"] for r in (await web.get("/api/requests?status=done")).json()] == ["Breaking Bad"]
    other = next(r for r in (await web.get("/api/requests")).json())
    assert (await web.delete(f"/api/requests/{other['_id']}", headers=H)).json() == {"ok": True}
    assert (await web.get("/api/requests")).json() == []


async def test_service_rejects_bad_input_directly(app):
    for kw in ({"title": " ", "year": None, "kind": None}, {"title": "Ok Title", "year": 5, "kind": None}):
        with pytest.raises(RequestError):
            await app.requests.submit(who="x", **kw)
