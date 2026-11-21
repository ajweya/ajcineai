import pytest

from ajcine import db as dbm
from ajcine.tmdb import PosterService, TmdbClient, poster_src, valid_image_url
from tests.conftest import make_msg


class FakeTmdb(TmdbClient):
    """Replaces the HTTP layer; records requests."""

    def __init__(self, settings, responses):
        super().__init__(settings)
        self.responses, self.calls = responses, []

    async def _get(self, path, params=None):
        self.calls.append((path, params))
        return self.responses.get(path)


@pytest.fixture
def tsettings(settings):
    return settings.with_overrides(tmdb_api_key="k")


def svc(app, tsettings, responses):
    app.posters = PosterService(tsettings, app.db, app.catalog, FakeTmdb(tsettings, responses))
    return app.posters


async def add(app, mid, name):
    await app.indexer.ingest_message(make_msg(-100, mid, name, unique=f"U{mid}"))


def test_poster_src_and_url_validation():
    assert poster_src("https://image.tmdb.org/t/p/w500/a.jpg") == "https://image.tmdb.org/t/p/w500/a.jpg"
    assert poster_src("tt1.jpg") == "/posters/tt1.jpg" and poster_src(None) is None
    assert valid_image_url("https://a.example/x.jpg")
    for bad in ("http://a.example/x.jpg", "javascript:alert(1)", "https://a b", "", None, "https://x"):
        assert not valid_image_url(bad)


async def test_imdb_title_gets_tmdb_poster_url_and_nothing_is_downloaded(app, tsettings, tmp_path):
    await add(app, 1, "Inception.2010.English.1080p.mkv")
    s = svc(app, tsettings, {"/find/tt1375666": {"movie_results": [
        {"id": 27205, "poster_path": "/abc.jpg", "backdrop_path": "/bk.jpg"}]}})
    assert await s.refresh_title("tt1375666") == "set"
    t = await app.catalog.get_title("tt1375666")
    assert t["poster"] == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert t["backdrop"] == "https://image.tmdb.org/t/p/w1280/bk.jpg"
    assert t["poster_source"] == "tmdb" and t["tmdb_id"] == 27205
    assert s.tmdb.calls == [("/find/tt1375666", {"external_source": "imdb_id"})]


async def test_series_uses_tv_results(app, tsettings):
    await add(app, 1, "Breaking.Bad.S01E01.English.720p.mkv")
    s = svc(app, tsettings, {"/find/tt0903747": {"movie_results": [{"id": 1, "poster_path": "/wrong.jpg"}],
                                                  "tv_results": [{"id": 1396, "poster_path": "/bb.jpg"}]}})
    await s.refresh_title("tt0903747")
    assert (await app.catalog.get_title("tt0903747"))["poster"].endswith("/w500/bb.jpg")


async def test_local_collection_needs_an_exact_name_match(app, tsettings):
    app.pipeline.s = app.pipeline.s.with_overrides(auto_local_collections=True)
    await add(app, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    tid = (await app.db[dbm.FILES].find_one({"_id": "-100:1"}))["title_id"]
    s = svc(app, tsettings, {"/search/movie": {"results": [
        {"id": 5, "title": "Kuttavum Shikshayum Reloaded", "release_date": "2015-01-01", "poster_path": "/no.jpg"},
        {"id": 6, "title": "Kuttavum Shikshayum", "release_date": "2015-06-01", "poster_path": "/yes.jpg"}]}})
    assert await s.refresh_title(tid) == "set"
    assert (await app.catalog.get_title(tid))["poster"].endswith("/yes.jpg")


async def test_local_collection_with_wrong_year_or_name_is_left_without_poster(app, tsettings):
    app.pipeline.s = app.pipeline.s.with_overrides(auto_local_collections=True)
    await add(app, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    tid = (await app.db[dbm.FILES].find_one({"_id": "-100:1"}))["title_id"]
    s = svc(app, tsettings, {"/search/movie": {"results": [
        {"id": 6, "title": "Kuttavum Shikshayum", "release_date": "1990-01-01", "poster_path": "/old.jpg"}]}})
    assert await s.refresh_title(tid) == "none"
    t = await app.catalog.get_title(tid)
    assert not t.get("poster") and t["tmdb_checked_at"]
    assert tid not in await s.pending()      # not retried until the recheck interval passes


async def test_manual_poster_is_never_overwritten(app, tsettings):
    await add(app, 1, "Inception.2010.English.1080p.mkv")
    await app.catalog.set_poster("tt1375666", "https://my.example/p.jpg", "manual")
    s = svc(app, tsettings, {"/find/tt1375666": {"movie_results": [{"id": 1, "poster_path": "/x.jpg"}]}})
    assert await s.refresh_title("tt1375666") == "manual"
    assert s.tmdb.calls == []
    assert await s.refresh_title("tt1375666", force=True) == "set"      # explicit admin action only


async def test_worker_picks_only_titles_without_posters(app, tsettings):
    await add(app, 1, "Inception.2010.English.1080p.mkv")
    await add(app, 2, "Breaking.Bad.S01E01.English.720p.mkv")
    await app.catalog.set_poster("tt0903747", "https://x.example/p.jpg", "manual")
    s = svc(app, tsettings, {})
    assert await s.pending() == ["tt1375666"]


async def test_disabled_without_key(app):
    assert app.posters.tmdb.enabled is False
    assert await app.posters.refresh_title("tt1375666") == "disabled"
    assert await app.posters.pending() is not None


async def test_http_layer_builds_authenticated_request_and_handles_errors(tsettings):
    class Resp:
        def __init__(self, status, body=None): self.status, self._b, self.headers = status, body, {}
        async def json(self): return self._b
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Sess:
        def __init__(self, resp): self.resp, self.seen = resp, []
        def get(self, url, params=None, headers=None):
            self.seen.append((url, params, headers)); return self.resp

    sess = Sess(Resp(200, {"ok": 1}))
    c = TmdbClient(tsettings, sess)
    assert await c._get("/find/tt1", {"external_source": "imdb_id"}) == {"ok": 1}
    url, params, headers = sess.seen[0]
    assert url == "https://api.themoviedb.org/3/find/tt1" and params["api_key"] == "k"
    tok = TmdbClient(tsettings.with_overrides(tmdb_api_key="", tmdb_read_token="TOK"), Sess(Resp(200, {})))
    await tok._get("/x")
    assert tok._session.seen[0][2]["Authorization"] == "Bearer TOK" and "api_key" not in tok._session.seen[0][1]
    assert await TmdbClient(tsettings, Sess(Resp(401)))._get("/x") is None
    assert await TmdbClient(tsettings.with_overrides(tmdb_api_key=""), Sess(Resp(200, {})))._get("/x") is None


async def _csp(app, settings):
    import httpx
    from ajcine.web import create_web_app
    app.settings = settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        return (await c.get("/healthz")).headers["content-security-policy"]


async def test_csp_allows_only_the_tmdb_image_host_when_ads_are_off(app, tsettings):
    csp = await _csp(app, tsettings.with_overrides(ads_enabled=False))
    assert "img-src 'self' data: https://image.tmdb.org;" in csp and "script-src 'self'" in csp


async def test_csp_allows_https_images_when_ads_are_on(app, tsettings):
    csp = await _csp(app, tsettings.with_overrides(ads_enabled=True))
    assert "img-src 'self' data: https:;" in csp and "script-src 'self'" in csp


async def test_poster_refresh_endpoint_reports_missing_config(app):
    import httpx
    from ajcine.users import hash_password
    from ajcine.web import create_web_app
    await app.db[dbm.ADMINS].insert_one({"username": "m", "password_hash": hash_password("password-m1234"),
                                         "role": "moderator", "active": True})
    await add(app, 1, "Inception.2010.English.1080p.mkv")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_web_app(app)), base_url="http://t") as c:
        await c.post("/api/login", json={"username": "m", "password": "password-m1234"})
        r = await c.post("/api/media/tt1375666/poster/refresh", headers={"X-Requested-With": "ajcine"})
        assert r.status_code == 400 and "TMDB" in r.text
