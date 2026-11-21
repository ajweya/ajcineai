import types
import pytest
from ajcine import db as dbm
from ajcine.ai.identify import AIResult
from tests.conftest import make_msg


class StubAI:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def available(self):
        return True

    async def normalise(self, filename, caption, parsed, max_wait=60):
        self.calls += 1
        return self.result


async def test_confident_match_is_identified_without_ai(app):
    app.pipeline.ai = StubAI(None)
    r = await app.pipeline.identify("Inception.2010.1080p.BluRay.x264.English.mkv", None)
    assert r.status == "identified" and r.title_id == "tt1375666" and app.pipeline.ai.calls == 0
    assert await app.db[dbm.MOVIES].find_one({"_id": "tt1375666"})


async def test_series_is_classified_as_series(app):
    r = await app.pipeline.identify("Breaking.Bad.S02E05.1080p.WEB-DL.mkv", None)
    assert r.status == "identified" and r.content_type == "series"
    assert await app.db[dbm.SERIES].find_one({"_id": "tt0903747"})


async def test_unknown_title_goes_to_review_not_catalog(app):
    app.pipeline.ai = None
    r = await app.pipeline.identify("Totally.Unknown.Film.2031.720p.mkv", None)
    assert r.status in ("unidentified", "pending_review") and r.title_id is None


async def test_ai_guess_is_only_used_after_imdb_verification(app):
    # filename is junk; the AI proposes "Inception" which IMDb verifies
    ai = StubAI(AIResult(title="Inception", year=2010, content_type="movie", confidence=0.9, model="m"))
    app.pipeline.ai = ai
    r = await app.pipeline.identify("incptn.hindi.720p.mkv", None)
    assert ai.calls == 1
    assert r.title_id in ("tt1375666", None)
    assert r.status in ("identified", "pending_review")


async def test_ai_cannot_invent_a_title_imdb_does_not_have(app):
    ai = StubAI(AIResult(title="Completely Invented Movie Name", year=2020, content_type="movie", confidence=0.99, model="m"))
    app.pipeline.ai = ai
    r = await app.pipeline.identify("zzz.qqq.720p.mkv", None)
    assert r.status != "identified" and r.title_id is None


async def test_imdb_outage_goes_to_review_instead_of_guessing(app):
    app.lookup.down = True
    r = await app.pipeline.identify("Inception.2010.1080p.mkv", None)
    assert r.status == "pending_review" and "unavailable" in r.reason


async def test_manual_correction_beats_automatic_identification(app):
    # the parser would say Inception; an admin decided that this file is Interstellar
    await app.db[dbm.CORRECTIONS].insert_one({"type": "candidate", "file_unique_id": "UNIQ1", "imdb_id": "tt0816692",
                                              "kind": "movie", "title": "Interstellar", "year": 2014, "active": True,
                                              "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    r = await app.pipeline.identify("Inception.2010.1080p.mkv", None, file_unique_id="UNIQ1")
    assert r.title_id == "tt0816692" and r.method == "manual"


async def test_manual_group_decision_persists_for_new_files(app):
    msg = make_msg(-100, 1, "Wierd.Name.2024.1080p.mkv")
    app.pipeline.ai = None
    assert await app.indexer.ingest_message(msg) == "indexed"
    fid = "-100:1"
    res = await app.review.approve(fid, "tt0816692", {"type": "test"}, scope="group")
    assert res["approved"] >= 1
    # a different file with the same parsed title is routed by the stored decision, no IMDb search needed
    before = app.lookup.searches
    msg2 = make_msg(-100, 2, "Wierd.Name.2024.720p.mkv")
    await app.indexer.ingest_message(msg2)
    f2 = await app.db[dbm.FILES].find_one({"_id": "-100:2"})
    assert f2["title_id"] == "tt0816692" and f2["status"] == "identified" and app.lookup.searches == before


async def test_per_file_edit_survives_reindex(app):
    await app.indexer.ingest_message(make_msg(-100, 10, "Breaking.Bad.S01E01.720p.mkv", unique="UB1"))
    await app.review.edit_file("-100:10", {"episode": 7, "quality": "1080p"}, {"type": "test"})
    f = await app.db[dbm.FILES].find_one({"_id": "-100:10"})
    assert f["episode"] == 7 and f["quality"] == "1080p"
    await app.indexer.ingest_message(make_msg(-100, 10, "Breaking.Bad.S01E01.720p.mkv", unique="UB1"), reindex=True)
    f = await app.db[dbm.FILES].find_one({"_id": "-100:10"})
    assert f["episode"] == 7 and f["quality"] == "1080p"
