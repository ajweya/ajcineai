import httpx
import pytest
from mongomock_motor import AsyncMongoMockClient

from ajcine import db as dbm
from ajcine.app import build_app
from ajcine.cache import Cache
from ajcine.config import Settings
from ajcine.tools.backup import create_backup
from ajcine.tools.split_db import split
from ajcine.users import hash_password
from ajcine.web import create_web_app
from tests.conftest import FakeTitles, make_msg
import fakeredis.aioredis


def multi(prefix="m"):
    c = AsyncMongoMockClient()
    return dbm.MultiDB({r: c[f"{prefix}_{r}"] for r in dbm.ROLES}), c


async def make_app(settings, db):
    a = await build_app(settings, db=db, cache=Cache(client=fakeredis.aioredis.FakeRedis(decode_responses=True)))
    f = FakeTitles(); a.lookup = a.pipeline.lookup = a.review.lookup = f
    return a


def test_every_collection_has_exactly_one_role():
    names = [c for cs in dbm.ROLES.values() for c in cs]
    assert len(names) == len(set(names))
    for const in ("MOVIES", "SERIES", "FILES", "USERS", "ADMINS", "SEARCH_HISTORY", "DELIVERIES", "SETTINGS", "REQUESTS",
                  "WEB_SESSIONS", "DELIVERY_TOKENS", "SOURCES", "JOBS"):
        assert getattr(dbm, const) in names, const


async def test_collections_are_routed_to_their_own_databases(settings):
    db, client = multi()
    app = await make_app(settings, db)
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.English.1080p.mkv", unique="U1"))
    import types
    await app.users.touch(types.SimpleNamespace(id=42, first_name="n", username="u"))
    await app.search.search("inception")
    await app.delivery.create_token(42, ["-100:1"], "tt1375666")
    for role, db_obj in db.dbs.items():
        have = set(await db_obj.list_collection_names())
        assert have <= set(dbm.ROLES[role]), (role, have)
    assert await db.dbs["catalog"][dbm.MOVIES].count_documents({}) == 1
    assert await db.dbs["files"][dbm.FILES].count_documents({}) == 1
    assert await db.dbs["activity"][dbm.SEARCH_HISTORY].count_documents({}) >= 1
    assert await db.dbs["users"][dbm.DELIVERY_TOKENS].count_documents({}) == 1
    assert await db.dbs["users"][dbm.USERS].count_documents({"_id": 42}) == 1
    assert await db.dbs["core"][dbm.SOURCES].count_documents({}) >= 0
    assert await db.dbs["files"][dbm.MOVIES].count_documents({}) == 0       # not duplicated elsewhere


async def test_full_pipeline_works_on_separate_databases(settings):
    db, _ = multi()
    app = await make_app(settings, db)
    for i, n in enumerate(["Inception.2010.Hindi.480p.mkv", "Inception.2010.English.1080p.mkv",
                           "Breaking.Bad.S01E01.English.720p.mkv"], 1):
        assert await app.indexer.ingest_message(make_msg(-100, i, n, unique=f"U{i}")) == "indexed"
    res = await app.search.search("breaking bad")
    assert res["items"][0]["id"] == "tt0903747"
    st = await app.catalog.stats()
    assert st["files"] == 3 and st["movies"] == 1 and st["series"] == 1
    tok = await app.delivery.create_token(7, ["-100:2"], "tt1375666")
    assert (await app.delivery.resolve_token(tok, 7))["file_ids"] == ["-100:2"]
    info = await db.ping()
    assert info == {r: True for r in dbm.ROLES}


async def test_backup_collects_from_every_database(settings, tmp_path):
    db, _ = multi()
    app = await make_app(settings, db)
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.English.1080p.mkv", unique="U1"))
    await app.db[dbm.USERS].insert_one({"_id": 5, "name": "x"})
    r = await create_backup(app.db, str(tmp_path))
    assert r["counts"][dbm.MOVIES] == 1 and r["counts"][dbm.FILES] == 1 and r["counts"][dbm.USERS] == 1


def test_db_targets_defaults_and_overrides():
    s = Settings(mongo_uri="mongodb://a:1", mongo_db="aj")
    assert set(s.db_targets().values()) == {("mongodb://a:1", "aj")}                      # single database
    s = Settings(mongo_uri="mongodb://a:1", mongo_db="aj", mongo_catalog_uri="mongodb://b:2",
                 mongo_files_db="files_only", mongo_users_uri="mongodb://c:3", mongo_users_db="people")
    t = s.db_targets()
    assert t["core"] == ("mongodb://a:1", "aj") and t["activity"] == ("mongodb://a:1", "aj")
    assert t["catalog"] == ("mongodb://b:2", "aj_catalog")
    assert t["files"] == ("mongodb://a:1", "files_only")
    assert t["users"] == ("mongodb://c:3", "people")
    bad = Settings(mongo_uri="mongodb://a:1", mongo_catalog_uri="http://nope", web_secret_key="k" * 40, api_id=1,
                   api_hash="x", bot_token="1:x", owner_ids=(1,))
    assert any("catalog" in p for p in bad.problems())


def test_from_env_reads_role_settings():
    s = Settings.from_env({"MONGO_CATALOG_URI": "mongodb://x", "MONGO_USERS_DB": "u", "MONGO_ACTIVITY_URI": "mongodb://y"})
    assert s.mongo_catalog_uri == "mongodb://x" and s.mongo_users_db == "u" and s.mongo_activity_uri == "mongodb://y"


def test_missing_role_is_rejected():
    c = AsyncMongoMockClient()
    with pytest.raises(ValueError):
        dbm.MultiDB({"core": c["x"]})


async def test_split_copies_an_existing_single_database(settings):
    c = AsyncMongoMockClient()
    legacy = c["legacy"]
    one = await make_app(settings, legacy)
    for i, n in enumerate(["Inception.2010.English.1080p.mkv", "Breaking.Bad.S01E01.English.720p.mkv"], 1):
        await one.indexer.ingest_message(make_msg(-100, i, n, unique=f"U{i}"))
    await one.search.search("inception")
    await legacy[dbm.ADMINS].insert_one({"username": "a", "role": "owner"})
    target = dbm.MultiDB({r: c[f"new_{r}"] for r in dbm.ROLES})
    dry = await split(legacy, target, apply=False)
    assert all(r["copied"] == 0 for r in dry) and await target.dbs["files"][dbm.FILES].count_documents({}) == 0
    rows = await split(legacy, target, apply=True)
    assert all(r["verified"] in (True, None) for r in rows)
    assert await target.dbs["catalog"][dbm.MOVIES].count_documents({}) == 1
    assert await target.dbs["files"][dbm.FILES].count_documents({}) == 2
    assert await target.dbs["users"][dbm.ADMINS].count_documents({}) == 1
    assert await legacy[dbm.FILES].count_documents({}) == 2                 # the source is untouched
    await split(legacy, target, apply=True)                                  # idempotent
    assert await target.dbs["files"][dbm.FILES].count_documents({}) == 2
    two = await make_app(settings, target)                                   # the app runs on the copy
    assert (await two.search.search("breaking bad"))["items"][0]["id"] == "tt0903747"


async def test_dashboard_shows_layout_without_credentials(settings):
    db, _ = multi()
    db.targets = {r: ("mongodb://user:s3cret@db.example.com:27017", f"m_{r}") for r in dbm.ROLES}
    app = await make_app(settings, db)
    await app.db[dbm.ADMINS].insert_one({"username": "v", "password_hash": hash_password("password-v1234"),
                                         "role": "viewer", "active": True})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        assert (await c.get("/api/databases")).status_code == 401
        await c.post("/api/login", json={"username": "v", "password": "password-v1234"})
        r = await c.get("/api/databases")
        assert r.status_code == 200 and "s3cret" not in r.text and "mongodb://" not in r.text
        rows = {x["role"]: x for x in r.json()}
        assert set(rows) == set(dbm.ROLES) and rows["catalog"]["host"] == "db.example.com"
        assert rows["files"]["ok"] in (True, False) and dbm.FILES in rows["files"]["documents"]
        assert (await c.get("/healthz")).json()["checks"]["mongo"] is True
