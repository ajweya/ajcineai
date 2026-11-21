"""Collection identity and file-level metadata.

A collection (a `loc_` or `tt` id) is one movie or one series; every upload is its own file record."""
import pytest

from ajcine import db as dbm
from ajcine.catalog import local_id
from ajcine.tools.collections import (COLLECTION_COLUMNS, FILE_COLUMNS, collection_row, file_row, migrate,
                                      render_table, report)
from tests.conftest import make_msg


@pytest.fixture
async def lapp(app):
    """App that groups titles IMDb does not know into local collections."""
    app.pipeline.s = app.pipeline.s.with_overrides(auto_local_collections=True)
    return app


async def add(app, mid, name, chat=-100, unique=None, **kw):
    r = await app.indexer.ingest_message(make_msg(chat, mid, name, unique=unique or f"U{chat}_{mid}", **kw))
    return r, f"{chat}:{mid}"


async def file(app, fid):
    return await app.db[dbm.FILES].find_one({"_id": fid})


async def collections(app):
    out = []
    for c in (dbm.MOVIES, dbm.SERIES):
        out += [d async for d in app.db[c].find({})]
    return out


# ---- 1. identity -----------------------------------------------------------------------------------
async def test_hindi_and_english_versions_share_one_collection(lapp):
    _, a = await add(lapp, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.WEB-DL.mkv")
    _, b = await add(lapp, 2, "Kuttavum.Shikshayum.2015.English.1080p.WEB-DL.mkv")
    fa, fb = await file(lapp, a), await file(lapp, b)
    assert fa["title_id"] == fb["title_id"] == fa["collection_id"] == fb["collection_id"]
    assert fa["title_id"].startswith("loc_")
    assert len(await collections(lapp)) == 1
    assert fa["languages"] == ["hi"] and fb["languages"] == ["en"]
    assert fa["quality"] == "720p" and fb["quality"] == "1080p"


async def test_collection_id_is_deterministic_and_matches_the_formula(lapp):
    _, a = await add(lapp, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    tid = (await file(lapp, a))["title_id"]
    assert tid == local_id("movie", "Kuttavum Shikshayum", 2015)
    assert local_id("movie", "Kuttavum Shikshayum", 2015) == local_id("movie", "kuttavum-shikshayum", 2015)


async def test_file_without_year_joins_the_existing_collection(lapp):
    _, a = await add(lapp, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    _, b = await add(lapp, 2, "Kuttavum Shikshayum English 480p.mkv")
    assert (await file(lapp, a))["title_id"] == (await file(lapp, b))["title_id"]
    assert len(await collections(lapp)) == 1


async def test_year_added_later_adopts_the_yearless_collection(lapp):
    _, a = await add(lapp, 1, "Kuttavum Shikshayum English 480p.mkv")
    first = (await file(lapp, a))["title_id"]
    _, b = await add(lapp, 2, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    assert (await file(lapp, b))["title_id"] == first
    assert (await lapp.catalog.get_title(first))["year"] == 2015


async def test_different_movies_and_remakes_never_share_an_id(lapp):
    ids = set()
    for i, n in enumerate(["Rangoon.Days.2015.Hindi.720p.mkv", "Rangoon.Days.2019.Hindi.720p.mkv",
                           "Mumbai.Nights.2015.Hindi.720p.mkv", "Rangoon.Days.S01E01.720p.mkv"], start=1):
        _, f = await add(lapp, i, n)
        ids.add((await file(lapp, f))["title_id"])
    assert len(ids) == 4


async def test_imdb_titles_group_all_languages_in_one_collection(app):
    _, a = await add(app, 1, "Inception.2010.Hindi.480p.mkv")
    _, b = await add(app, 2, "Inception.2010.English.1080p.mkv")
    _, c = await add(app, 3, "Inception.2010.[Hin+Eng].720p.mkv")
    assert {(await file(app, x))["collection_id"] for x in (a, b, c)} == {"tt1375666"}
    assert len([t for t in await collections(app) if t["_id"].startswith("loc_")]) == 0


async def test_unmatched_files_stay_in_review_when_auto_local_is_off(app):
    r, f = await add(app, 1, "Kuttavum.Shikshayum.2015.Hindi.720p.mkv")
    d = await file(app, f)
    assert d["status"] in ("pending_review", "unidentified") and d["collection_id"] is None


# ---- 2/3. per-file metadata and languages -----------------------------------------------------------
async def test_each_file_keeps_its_own_metadata(app):
    ids = []
    for i, (n, size) in enumerate([("Inception.2010.Hindi.480p.x264.mkv", 400_000_000),
                                   ("Inception.2010.English.1080p.BluRay.HEVC.mkv", 3_000_000_000),
                                   ("Inception.2010.English.2160p.WEB-DL.mkv", 9_000_000_000)], start=1):
        _, f = await add(app, i, n, size=size)
        ids.append(f)
    docs = [await file(app, f) for f in ids]
    assert [d["quality"] for d in docs] == ["480p", "1080p", "2160p"]
    assert [d["languages"] for d in docs] == [["hi"], ["en"], ["en"]]
    assert len({d["file_name"] for d in docs}) == 3 and len({d["file_id"] for d in docs}) == 3
    assert len({d["_id"] for d in docs}) == 3 and all(d["source_message_id"] for d in docs)
    assert docs[1]["video_codec"] and docs[2]["size"] == 9_000_000_000
    t = await app.catalog.get_title("tt1375666")
    assert t["qualities"] == ["2160p", "1080p", "480p"] and "quality" not in t and "resolution" not in t
    assert sorted(t["languages"]) == ["en", "hi"]


@pytest.mark.parametrize("name,audio,subs", [
    ("Inception.2010.[Hin+Eng].720p.mkv", ["hi", "en"], []),
    ("Inception.2010.Hindi-English.720p.mkv", ["hi", "en"], []),
    ("Inception.2010.[Tam+Tel+Mal+Hin].720p.mkv", ["ta", "te", "ml", "hi"], []),
    ("Inception.2010.Malayalam.720p.mkv", ["ml"], []),
    ("Inception.2010.Tamil.Telugu.720p.mkv", ["ta", "te"], []),
    ("Inception.2010.English.720p.Hindi.Subs.mkv", ["en"], ["hi"]),
    ("Inception.2010.720p.ESub.mkv", [], ["en"]),
    ("Inception.2010.Hindi.720p.English.Subtitles.mkv", ["hi"], ["en"]),
])
async def test_audio_languages_are_separate_from_subtitles(app, name, audio, subs):
    _, f = await add(app, 1, name)
    d = await file(app, f)
    assert d["collection_id"] == "tt1375666"
    assert [c for c in d["languages"] if c != "xx"] == audio
    assert d["subtitles"] == subs
    assert d["languages_source"] == ("filename" if audio else "unknown")


async def test_multilingual_file_creates_no_extra_collection(lapp):
    _, a = await add(lapp, 1, "Kuttavum.Shikshayum.2015.[Hin+Eng].720p.mkv")
    _, b = await add(lapp, 2, "Kuttavum.Shikshayum.2015.Hindi.480p.mkv")
    d = await file(lapp, a)
    assert d["languages"] == ["hi", "en"] and d["multi_audio"] is True
    assert d["collection_id"] == (await file(lapp, b))["collection_id"]
    assert len(await collections(lapp)) == 1
    langs = await lapp.catalog.languages_for(d["collection_id"])
    assert {l["code"]: l["files"] for l in langs} == {"hi": 2, "en": 1}


async def test_manual_language_override_is_recorded_as_manual(app):
    _, f = await add(app, 1, "Inception.2010.720p.mkv")
    await app.review.edit_file(f, {"languages": ["ml"]}, {"type": "web", "username": "t"})
    d = await file(app, f)
    assert d["languages"] == ["ml"] and d["languages_source"] == "manual"


# ---- 4. quality ---------------------------------------------------------------------------------------
async def test_480p_and_720p_versions_are_separate_options(app):
    await add(app, 1, "Inception.2010.English.480p.mkv")
    await add(app, 2, "Inception.2010.English.720p.mkv")
    q = await app.catalog.qualities_for("tt1375666", "en")
    assert [x["quality"] for x in q] == ["720p", "480p"]
    assert [f["quality"] for f in await app.catalog.files_for("tt1375666", "en", "480p")] == ["480p"]
    assert [f["quality"] for f in await app.catalog.files_for("tt1375666", "en", "720p")] == ["720p"]


# ---- 5. series ------------------------------------------------------------------------------------------
async def test_series_episodes_languages_and_qualities(app):
    names = ["Breaking.Bad.S01E01.English.720p.mkv", "Breaking.Bad.S01E01.English.1080p.mkv",
             "Breaking.Bad.S01E01.Hindi.720p.mkv", "Breaking.Bad.S01E02.English.720p.mkv",
             "Breaking.Bad.S02E01.English.480p.mkv"]
    for i, n in enumerate(names, start=1):
        await add(app, i, n)
    cat = app.catalog
    assert {x["season"]: x["episodes"] for x in await cat.seasons_of("tt0903747")} == {1: 2, 2: 1}
    assert [e["episode"] for e in await cat.episodes_of("tt0903747", 1)] == [1, 2]
    langs = await cat.languages_for("tt0903747", season=1, episode=1)
    assert {l["code"] for l in langs} == {"en", "hi"}
    q = await cat.qualities_for("tt0903747", "en", season=1, episode=1)
    assert [x["quality"] for x in q] == ["1080p", "720p"]
    f = (await cat.files_for("tt0903747", "en", "1080p", season=1, episode=1))[0]
    assert f["season"] == 1 and f["episode"] == 1 and f["_id"] == "-100:2" and f["collection_id"] == "tt0903747"
    assert (await cat.get_title("tt0903747"))["file_count"] == 5


async def test_local_series_groups_every_episode_under_one_collection(lapp):
    ids = set()
    for i, n in enumerate(["Night.Shift.S01E01.Hindi.720p.mkv", "Night.Shift.S01E02.Hindi.720p.mkv",
                           "Night.Shift.S01E01.English.1080p.mkv", "Night.Shift.S02E01.English.480p.mkv"], start=1):
        _, f = await add(lapp, i, n)
        d = await file(lapp, f)
        ids.add(d["collection_id"])
        assert d["season"] is not None and d["episode"] is not None
    assert len(ids) == 1 and ids.pop().startswith("loc_")
    assert len(await collections(lapp)) == 1


# ---- 6. integrity ----------------------------------------------------------------------------------------
async def test_duplicate_telegram_message_is_not_indexed_twice(app):
    r1, f = await add(app, 1, "Inception.2010.English.1080p.mkv")
    r2, _ = await add(app, 1, "Inception.2010.English.1080p.mkv")
    assert (r1, r2) == ("indexed", "duplicate")
    assert await app.db[dbm.FILES].count_documents({}) == 1


async def test_same_upload_in_two_chats_is_one_record_with_an_alt_source(app):
    await add(app, 1, "Inception.2010.English.1080p.mkv", chat=-100, unique="SAME")
    r, _ = await add(app, 9, "Inception.2010.English.1080p.mkv", chat=-200, unique="SAME")
    assert r == "duplicate"
    assert await app.db[dbm.FILES].count_documents({}) == 1
    d = await file(app, "-100:1")
    assert d["alt_sources"][0]["chat_id"] == -200 and d["dup_count"] == 1


async def test_identical_filenames_with_different_uploads_stay_separate_files(app):
    await add(app, 1, "Inception.2010.English.1080p.mkv", unique="A")
    await add(app, 2, "Inception.2010.English.1080p.mkv", unique="B")
    assert await app.db[dbm.FILES].count_documents({"collection_id": "tt1375666"}) == 2
    assert len(await collections(app)) == 1


async def test_similar_titles_do_not_merge(app):
    await add(app, 1, "The.Matrix.1999.English.720p.mkv")
    await add(app, 2, "The.Matrix.Reloaded.2003.English.720p.mkv")
    a, b = await file(app, "-100:1"), await file(app, "-100:2")
    assert a["collection_id"] == "tt0133093" and b["collection_id"] == "tt0234215"


async def test_unique_index_blocks_duplicate_source_messages(app):
    await add(app, 1, "Inception.2010.English.1080p.mkv")
    doc = await file(app, "-100:1")
    doc2 = {k: v for k, v in doc.items() if k != "_id"} | {"_id": "other-id"}
    with pytest.raises(Exception) as e:
        await app.db[dbm.FILES].insert_one(doc2)
    assert type(e.value).__name__ == "DuplicateKeyError"


async def test_indexes_for_collection_filename_season_episode_exist(app):
    info = await app.db[dbm.FILES].index_information()
    keys = [tuple(k for k, _ in v["key"]) for v in info.values()]
    assert ("collection_id",) in keys and ("file_name",) in keys
    assert ("collection_id", "season", "episode", "quality") in keys
    assert ("title_id", "season", "episode") in keys


# ---- migration --------------------------------------------------------------------------------------------
async def test_migration_links_files_and_merges_split_local_collections(app):
    cat = app.catalog
    from ajcine.matching.models import Candidate
    a = await cat.upsert_title(Candidate(local_id("movie", "Old Split", 2001), "Old Split", 2001, "movie"), manual=True)
    b = await cat.upsert_title(Candidate(local_id("movie", "Old Split", None), "Old Split", None, "movie"), manual=True)
    other = await cat.upsert_title(Candidate(local_id("movie", "Old Split Two", None), "Old Split Two", None, "movie"), manual=True)
    for i, (tid, n) in enumerate([(a["_id"], "x.720p.mkv"), (b["_id"], "x.480p.mkv"), (other["_id"], "y.480p.mkv")], 1):
        await app.db[dbm.FILES].insert_one({"_id": f"-1:{i}", "source_chat_id": -1, "source_message_id": i,
                                            "file_unique_id": f"M{i}", "file_name": n, "title_id": tid,
                                            "status": "identified", "deleted": False, "missing": False,
                                            "languages": ["hi"], "quality": n.split(".")[1]})
        await cat.refresh_title_summary(tid)
    await app.db[dbm.MOVIES].update_many({}, {"$unset": {"match_key": ""}})
    dry = await migrate(app.db, apply=False)
    assert dry["merged_groups"] == 1 and await app.db[dbm.FILES].count_documents({"collection_id": {"$exists": True}}) == 0
    assert len(await collections(app)) == 3
    st = await migrate(app.db, apply=True)
    assert st["files_linked"] == 3 and st["merged_files"] == 1
    cols = await collections(app)
    assert len(cols) == 2
    keep = await cat.get_title(a["_id"]) or await cat.get_title(b["_id"])
    assert keep["file_count"] == 2 and sorted(keep["qualities"]) == ["480p", "720p"]
    assert await app.db[dbm.FILES].count_documents({"deleted": {"$ne": True}}) == 3  # nothing destroyed
    assert {f["collection_id"] async for f in app.db[dbm.FILES].find({"title_id": keep["_id"]})} == {keep["_id"]}
    again = await migrate(app.db, apply=True)
    assert again["merged_groups"] == 0 and again["files_linked"] == 0


# ---- 7. output -------------------------------------------------------------------------------------------------
async def test_report_separates_collection_and_file_metadata_with_fixed_columns(app):
    await add(app, 1, "Inception.2010.Hindi.480p.mkv")
    await add(app, 2, "Inception.2010.English.1080p.mkv")
    rep = await report(app.db, "tt1375666")
    row = rep["collections"][0]
    assert tuple(row) == COLLECTION_COLUMNS
    assert row["Collection ID"] == "tt1375666" and row["Files"] == 2
    assert row["Languages"] == "English, Hindi" and row["Qualities"] == "1080p, 480p"
    frows = rep["files"]["tt1375666"]
    assert all(tuple(r) == FILE_COLUMNS for r in frows)
    assert sorted((r["Resolution"], r["Audio"]) for r in frows) == [("1080p", "English"), ("480p", "Hindi")]
    assert all(set(r).isdisjoint({"Files", "Qualities", "Languages"}) for r in frows)
    text = render_table(FILE_COLUMNS, frows)
    assert text.splitlines()[0].split()[0] == "Season/Episode" and "Inception.2010.English.1080p.mkv" in text
    assert "Inception.2010.Hindi.480p.mkv" not in render_table(COLLECTION_COLUMNS, rep["collections"])
