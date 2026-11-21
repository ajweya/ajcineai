"""The TMDB-backed title service (HTTP mocked): search, IMDb-id mapping, caching, errors, episodes."""
import pytest

from ajcine import db as dbm
from ajcine.matching import LookupUnavailable, MatchQuery, TitleService
from ajcine.tmdb import TmdbClient, TmdbError

GENRES = {"genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}, {"id": 18, "name": "Drama"}]}
INCEPTION = {"id": 27205, "title": "Inception", "original_title": "Inception", "release_date": "2010-07-15",
             "vote_average": 8.37, "vote_count": 36000, "genre_ids": [28, 878], "overview": "A thief enters dreams."}
INCEPTION_D = {**INCEPTION, "imdb_id": "tt1375666", "runtime": 148, "genres": [{"id": 28, "name": "Action"}],
               "alternative_titles": {"titles": [{"title": "Origen"}, {"title": "Inception"}]}}
BB = {"id": 1396, "name": "Breaking Bad", "original_name": "Breaking Bad", "first_air_date": "2008-01-20",
      "vote_average": 8.9, "vote_count": 14000, "genre_ids": [18]}
BB_D = {**BB, "external_ids": {"imdb_id": "tt0903747"}, "episode_run_time": [47], "genres": [{"id": 18, "name": "Drama"}],
        "alternative_titles": {"results": [{"title": "Metastasis"}]}}
NO_IMDB = {"id": 999, "title": "Inceptio Obscura", "release_date": "2010-01-01", "genre_ids": []}


class FakeTmdb:
    enabled = True

    def __init__(self, routes=None, fail=None):
        self.calls, self.fail = [], fail
        self.routes = routes or {
            "/genre/movie/list": GENRES, "/genre/tv/list": GENRES,
            "/search/movie": {"results": [INCEPTION, NO_IMDB]}, "/search/tv": {"results": [BB]},
            "/movie/27205": INCEPTION_D, "/tv/1396": BB_D,
            "/movie/999": {**NO_IMDB, "imdb_id": None},
            "/find/tt1375666": {"movie_results": [INCEPTION], "tv_results": []},
            "/find/tt0903747": {"movie_results": [], "tv_results": [BB]},
            "/tv/1396/season/1": {"episodes": [{"episode_number": 1, "name": "Pilot", "air_date": "2008-01-20", "overview": "x"},
                                               {"episode_number": 2, "name": "Cat's in the Bag...", "air_date": "2008-01-27"}]},
        }

    async def _get(self, path, params=None, strict=False):
        self.calls.append((path, dict(params or {})))
        if self.fail:
            raise TmdbError(self.fail)
        return self.routes.get(path)

    async def close(self):
        pass


@pytest.fixture
def titles(app, settings):
    return TitleService(settings, app.db, None, FakeTmdb())


async def test_search_returns_imdb_keyed_candidates_with_tmdb_data(titles):
    ranked = await titles.search(MatchQuery("Inception", 2010, "movie"))
    top = ranked[0]
    assert (top.imdb_id, top.title, top.year, top.kind) == ("tt1375666", "Inception", 2010, "movie")
    assert top.rating == 8.4 and top.votes == 36000 and top.runtime == 148 and top.genres == ["Action"]
    assert "Origen" in top.alt_titles and "Inception" not in top.alt_titles
    assert all(c.imdb_id.startswith("tt") for c in ranked)          # a result without an IMDb id is never offered
    assert not any(c.title == "Inceptio Obscura" for c in ranked)


async def test_series_search_and_both_kinds_when_unknown(titles):
    ranked = await titles.search(MatchQuery("Breaking Bad"))
    assert ranked[0].imdb_id == "tt0903747" and ranked[0].kind == "series" and ranked[0].runtime == 47
    paths = [p for p, _ in titles.tmdb.calls]
    assert "/search/movie" in paths and "/search/tv" in paths
    titles.tmdb.calls.clear()
    await titles.search(MatchQuery("Breaking Bad", None, "series"))
    assert "/search/movie" not in [p for p, _ in titles.tmdb.calls]


async def test_results_are_cached_and_cache_survives_without_tmdb(titles, app, settings):
    await titles.search(MatchQuery("Inception", 2010, "movie"))
    assert await app.db[dbm.TITLE_CACHE].count_documents({}) == 1
    offline = TitleService(settings, app.db, None, FakeTmdb(fail="down"))
    again = await offline.search(MatchQuery("Inception", 2010, "movie"))   # served from the cache, no TMDB call
    assert again[0].imdb_id == "tt1375666" and offline.tmdb.calls == []


async def test_wrong_year_in_file_name_retries_without_the_year(titles):
    ranked = await titles.search(MatchQuery("Inception", 1999, "movie"))
    assert ranked and ranked[0].imdb_id == "tt1375666"
    years = [p.get("year") for path, p in titles.tmdb.calls if path == "/search/movie"]
    assert years[0] == 1999 or years[0] is None


async def test_get_by_imdb_id_and_unknown_id(titles):
    c = await titles.get("tt1375666")
    assert c.title == "Inception" and c.imdb_id == "tt1375666"
    assert await titles.get("tt9999999") is None
    assert await titles.get("not-an-id") is None


async def test_episode_titles_and_season_cache(titles):
    assert (await titles.episode("tt0903747", 1, 2))["title"] == "Cat's in the Bag..."
    before = len(titles.tmdb.calls)
    assert (await titles.episode("tt0903747", 1, 1))["title"] == "Pilot"      # whole season was cached by the first call
    assert len(titles.tmdb.calls) == before
    assert await titles.episode("tt0903747", 1, 40) is None


async def test_tmdb_trouble_raises_lookup_unavailable_but_episodes_degrade(app, settings):
    svc = TitleService(settings, app.db, None, FakeTmdb(fail="rate limited by TMDB"))
    with pytest.raises(LookupUnavailable):
        await svc.search(MatchQuery("Inception"))
    with pytest.raises(LookupUnavailable):
        await svc.get("tt1375666")
    assert await svc.episode("tt0903747", 1, 1) is None
    assert (await svc.probe())["ok"] is False


async def test_probe_reports_missing_configuration(app, settings):
    svc = TitleService(settings, app.db, None, TmdbClient(settings))        # no key in the test settings
    assert not svc.enabled
    assert "TMDB_API_KEY" in (await svc.probe())["error"]
    with pytest.raises(LookupUnavailable):
        await svc.search(MatchQuery("Inception"))


async def test_strict_client_distinguishes_not_found_from_failure(settings):
    class Resp:
        def __init__(self, status, body=None):
            self.status, self._b, self.headers = status, body or {}, {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return self._b

    class Sess:
        def __init__(self, status):
            self.status = status

        def get(self, *a, **k):
            return Resp(self.status, {"ok": 1})

    s = settings.with_overrides(tmdb_api_key="k")
    assert await TmdbClient(s, Sess(404))._get("/x", strict=True) is None
    assert await TmdbClient(s, Sess(200))._get("/x", strict=True) == {"ok": 1}
    with pytest.raises(TmdbError, match="rejected"):
        await TmdbClient(s, Sess(401))._get("/x", strict=True)
    assert await TmdbClient(s, Sess(401))._get("/x") is None


async def test_catalog_stores_tmdb_rating_not_an_imdb_rating(app):
    c = (await app.lookup.search(MatchQuery("Inception", 2010, "movie")))[0]
    doc = await app.catalog.upsert_title(c)
    assert doc["rating"] == 8.8 and "imdb_rating" not in doc
