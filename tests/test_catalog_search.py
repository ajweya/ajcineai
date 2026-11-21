import pytest
from ajcine import db as dbm
from tests.conftest import make_msg


async def index(app, names, chat=-100, start=1):
    for i, n in enumerate(names, start):
        await app.indexer.ingest_message(make_msg(chat, i, n, unique=f"U{chat}{i}"))


async def test_movie_versions_are_grouped_with_real_languages_and_qualities(app):
    await index(app, [
        "Inception.2010.English.480p.BluRay.mkv", "Inception.2010.English.1080p.BluRay.x264.mkv",
        "Inception.2010.2160p.UHD.BluRay.English.mkv", "Inception (2010) Malayalam 720p WEB-DL.mkv",
        "Inception.2010.Hindi.480p.HDRip.mkv"])
    movies = [m async for m in app.db[dbm.MOVIES].find({})]
    assert len(movies) == 1 and movies[0]["file_count"] == 5
    langs = {l["code"] for l in await app.catalog.languages_for("tt1375666")}
    assert langs == {"en", "ml", "hi"}                      # exactly what exists
    q_en = [q["quality"] for q in await app.catalog.qualities_for("tt1375666", "en")]
    assert q_en == ["2160p", "1080p", "480p"]               # no 720p for English: not indexed
    q_ml = [q["quality"] for q in await app.catalog.qualities_for("tt1375666", "ml")]
    assert q_ml == ["720p"]
    assert await app.catalog.languages_for("tt1375666", season=None) and not [
        l for l in await app.catalog.languages_for("tt1375666") if l["code"] == "ta"]


async def test_series_hierarchy_and_missing_episodes(app):
    await index(app, [
        "Breaking.Bad.S01E01.720p.WEB-DL.English.mkv", "Breaking.Bad.S01E02.720p.WEB-DL.English.mkv",
        "Breaking.Bad.S01E04.1080p.WEB-DL.English.mkv", "Breaking.Bad.S01E04.720p.WEB-DL.Hindi.mkv",
        "Breaking.Bad.S02E01.1080p.WEB-DL.English.mkv", "Breaking.Bad.S02.Complete.1080p.BluRay.English.mkv",
        "Breaking Bad Complete Series 720p BluRay English.mkv"])
    sid = "tt0903747"
    seasons = await app.catalog.seasons_of(sid)
    assert [(s["season"], s["episodes"]) for s in seasons] == [(1, 3), (2, 1)]
    assert [e["episode"] for e in await app.catalog.episodes_of(sid, 1)] == [1, 2, 4]
    assert await app.catalog.missing_episodes(sid) == {1: [3]}
    # each episode has its own languages/qualities
    assert {l["code"] for l in await app.catalog.languages_for(sid, 1, 4)} == {"en", "hi"}
    assert {l["code"] for l in await app.catalog.languages_for(sid, 1, 1)} == {"en"}
    assert await app.catalog.has_season_pack(sid, 2) and await app.catalog.has_complete_pack(sid)
    # episode lists never include packs
    assert [e["episode"] for e in await app.catalog.episodes_of(sid, 2)] == [1]
    assert await app.db[dbm.EPISODES].count_documents({"series_id": sid}) == 4


async def test_unrelated_series_are_not_combined(app):
    await index(app, ["The.Office.2005.S01E01.720p.mkv", "Breaking.Bad.S01E01.720p.mkv"])
    ids = {f["title_id"] async for f in app.db[dbm.FILES].find({"status": "identified"})}
    assert "tt0903747" in ids and len(ids) == 2


async def test_duplicates_are_detected_by_unique_id_and_by_version(app):
    await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.1080p.mkv", unique="SAME"))
    out = await app.indexer.ingest_message(make_msg(-200, 9, "Inception.2010.1080p.mkv", unique="SAME"))
    assert out == "duplicate"
    f = await app.db[dbm.FILES].find_one({"_id": "-100:1"})
    assert f["dup_count"] == 1 and f["alt_sources"][0]["chat_id"] == -200
    assert await app.indexer.ingest_message(make_msg(-100, 1, "Inception.2010.1080p.mkv", unique="SAME")) == "duplicate"
    # two different uploads of the same version are reported as a duplicate group
    await app.indexer.ingest_message(make_msg(-100, 3, "Inception.2010.1080p.WEB.mkv", unique="OTHER"))
    groups = await app.catalog.duplicate_groups()
    assert groups and groups[0]["count"] == 2


async def test_search_exact_partial_typo_alt_and_filters(app):
    await index(app, ["Inception.2010.English.1080p.mkv", "Premalu.2024.Malayalam.1080p.mkv",
                      "Breaking.Bad.S01E01.English.720p.mkv", "The.Matrix.1999.English.720p.mkv",
                      "The.Matrix.Reloaded.2003.English.720p.mkv"])
    s = app.search
    assert (await s.search("inception", record=False, use_ai=False))["items"][0]["id"] == "tt1375666"
    assert (await s.search("incepton", record=False, use_ai=False))["items"][0]["id"] == "tt1375666"   # typo
    assert (await s.search("premaalu", record=False, use_ai=False))["items"][0]["id"] == "tt26581740"  # transliteration
    assert (await s.search("break", record=False, use_ai=False))["items"][0]["id"] == "tt0903747"      # partial
    assert (await s.search("matrix 1999", record=False, use_ai=False))["items"][0]["id"] == "tt0133093"  # year hint
    series_only = await s.search("breaking bad", kind="series", record=False, use_ai=False)
    assert [i["id"] for i in series_only["items"]] == ["tt0903747"]
    assert (await s.search("inception", kind="series", record=False, use_ai=False))["items"] == []
    ml = await s.search("premalu", language="ml", record=False, use_ai=False)
    assert ml["items"][0]["languages"] == ["ml"]


async def test_search_only_returns_titles_with_real_files(app):
    from ajcine.matching.models import Candidate
    await app.catalog.upsert_title(Candidate("tt0133093", "The Matrix", 1999, "movie"))   # no files
    assert (await app.search.search("matrix", record=False, use_ai=False))["items"] == []


async def test_search_cache_invalidates_when_catalog_changes(app):
    await index(app, ["Inception.2010.English.1080p.mkv"])
    r1 = await app.search.search("inception", record=False, use_ai=False)
    assert r1["total"] == 1
    await index(app, ["Premalu.2024.Malayalam.1080p.mkv"], start=50)
    assert (await app.search.search("premalu", record=False, use_ai=False))["total"] == 1


async def test_search_history_recent_and_popular(app):
    await index(app, ["Inception.2010.English.1080p.mkv"])
    await app.search.search("inception", user_id=5, use_ai=False)
    await app.search.search("inception", user_id=5, use_ai=False)
    assert await app.search.history(5) == ["inception"]
    assert [i["id"] for i in await app.search.recent()] == ["tt1375666"]
    from ajcine.catalog import now
    await app.db[dbm.DELIVERIES].insert_one({"user_id": 5, "title_id": "tt1375666", "ts": now()})
    assert [i["id"] for i in await app.search.popular()] == ["tt1375666"]


async def test_pagination(app):
    names = [f"Inception.2010.English.1080p.mkv"]
    await index(app, names)
    r = await app.search.search("inception", record=False, use_ai=False, page=9)
    assert r["page"] == 1 and r["pages"] == 1


async def test_merge_titles_moves_files(app):
    await index(app, ["Inception.2010.English.1080p.mkv", "Interstellar.2014.English.1080p.mkv"])
    moved = await app.catalog.merge_titles("tt0816692", "tt1375666")
    assert moved == 1
    assert await app.db[dbm.MOVIES].count_documents({}) == 1
    assert (await app.catalog.get_title("tt1375666"))["file_count"] == 2
    assert "Interstellar" in (await app.catalog.get_title("tt1375666"))["alt_titles"]


async def test_deleted_source_message_removes_file_from_navigation(app):
    import types
    await index(app, ["Inception.2010.English.1080p.mkv", "Inception.2010.Hindi.720p.mkv"])
    await app.indexer.refresh_sources()
    app.indexer.live_chats = {-100}
    await app.indexer.on_deleted(None, [types.SimpleNamespace(id=2, chat=types.SimpleNamespace(id=-100))])
    assert {l["code"] for l in await app.catalog.languages_for("tt1375666")} == {"en"}
    assert (await app.catalog.get_title("tt1375666"))["file_count"] == 1
