import asyncio
import types
import pytest
from ajcine import db as dbm
from tests.conftest import make_msg


class FakeClient:
    def __init__(self, messages, die_after=None):
        self.messages = {m.id: m for m in messages}
        self.calls = 0
        self.die_after = die_after

    async def get_messages(self, chat_id, ids):
        self.calls += 1
        if self.die_after and self.calls > self.die_after:
            raise asyncio.CancelledError()
        return [self.messages.get(i, types.SimpleNamespace(id=i, empty=True)) for i in ids]

    async def get_chat(self, ref):
        return types.SimpleNamespace(id=-100, title="Test Channel", username="testchan", type="channel")


def build(n=35):
    msgs = []
    for i in range(1, n + 1):
        if i % 5 == 0:
            msgs.append(types.SimpleNamespace(id=i, empty=False, service="x", chat=types.SimpleNamespace(id=-100)))  # service msgs
        else:
            msgs.append(make_msg(-100, i, f"Inception.2010.English.{('480p','720p','1080p')[i % 3]}.part{i}.mkv", unique=f"U{i}"))
    return msgs


async def prep(app, client):
    app.indexer.get_client = lambda: client
    app.indexer.s = app.settings.with_overrides(index_batch_size=10, index_batch_delay=0.0)
    await app.db[dbm.SOURCES].insert_one({"_id": -100, "title": "T", "enabled": True, "live": True, "stats": {}, "last_message_id": 0})


async def wait(app, jid, statuses=("done", "failed", "cancelled")):
    for _ in range(300):
        j = await app.db[dbm.JOBS].find_one({"_id": jid})
        if j["status"] in statuses:
            return j
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def test_history_job_indexes_everything_and_counts(app):
    c = FakeClient(build())
    await prep(app, c)
    jid = await app.indexer.start_job(-100, "history", 1, 35)
    j = await wait(app, jid)
    assert j["status"] == "done" and j["processed"] == 35 and j["indexed"] == 28 and j["skipped"] == 7
    assert await app.db[dbm.FILES].count_documents({}) == 28


async def test_job_survives_restart_and_resumes_from_checkpoint(app):
    c = FakeClient(build(), die_after=2)           # process "crashes" on the 3rd batch
    await prep(app, c)
    jid = await app.indexer.start_job(-100, "history", 1, 35)
    t = app.indexer.tasks[jid]
    with pytest.raises(asyncio.CancelledError):
        await t
    j = await app.db[dbm.JOBS].find_one({"_id": jid})
    assert j["status"] == "running" and j["cursor"] == 21 and j["processed"] == 20
    done_before = await app.db[dbm.FILES].count_documents({})
    assert 0 < done_before < 28
    # "restart": new client works, resume() re-queues the running job and continues at the checkpoint
    app.indexer.get_client = lambda: FakeClient(build())
    app.indexer.tasks.clear()
    assert await app.indexer.resume() == 1
    j = await wait(app, jid)
    assert j["status"] == "done" and j["processed"] == 35
    assert await app.db[dbm.FILES].count_documents({}) == 28          # nothing lost, nothing duplicated


async def test_reindexing_same_range_creates_no_duplicates(app):
    await prep(app, FakeClient(build()))
    j1 = await wait(app, await app.indexer.start_job(-100, "history", 1, 35))
    j2 = await wait(app, await app.indexer.start_job(-100, "history", 1, 35))
    assert j2["duplicates"] == 28 and await app.db[dbm.FILES].count_documents({}) == 28


async def test_pause_and_resume(app):
    await prep(app, FakeClient(build()))
    app.indexer.s = app.indexer.s.with_overrides(index_batch_delay=0.05)
    jid = await app.indexer.start_job(-100, "history", 1, 35)
    await asyncio.sleep(0.07)
    assert await app.indexer.pause_job(jid)
    j = await wait(app, jid, ("paused",))
    mid = j["processed"]
    assert 0 < mid < 35
    app.indexer.tasks[jid] and await asyncio.sleep(0.15)
    assert await app.indexer.resume_job(jid)
    j = await wait(app, jid)
    assert j["status"] == "done" and j["processed"] == 35


async def test_access_loss_fails_job_cleanly(app):
    class Denied(FakeClient):
        async def get_messages(self, chat_id, ids):
            class ChannelPrivate(Exception):
                pass
            raise ChannelPrivate("no")
    await prep(app, Denied([]))
    j = await wait(app, await app.indexer.start_job(-100, "history", 1, 20))
    assert j["status"] == "failed" and "lost access" in j["error"]


async def test_only_one_active_job_per_source(app):
    await prep(app, FakeClient(build()))
    app.indexer.s = app.indexer.s.with_overrides(index_batch_delay=0.2)
    await app.indexer.start_job(-100, "history", 1, 35)
    with pytest.raises(ValueError):
        await app.indexer.start_job(-100, "history", 1, 35)
    await app.indexer.cancel_jobs(-100)


async def test_live_indexing_only_for_enabled_sources(app):
    await prep(app, FakeClient([]))
    await app.indexer.refresh_sources()
    await app.indexer.on_message(None, make_msg(-100, 500, "Inception.2010.English.1080p.mkv"))
    await app.indexer.on_message(None, make_msg(-999, 1, "Inception.2010.English.720p.mkv"))
    assert await app.db[dbm.FILES].count_documents({}) == 1
    src = await app.db[dbm.SOURCES].find_one({"_id": -100})
    assert src["last_message_id"] == 500 and src["stats"]["indexed"] == 1


async def test_non_video_documents_are_skipped(app):
    await prep(app, FakeClient([]))
    m = make_msg(-100, 1, "readme.pdf", mime="application/pdf")
    assert await app.indexer.ingest_message(m) == "skipped"
    assert await app.indexer.ingest_message(make_msg(-100, 2, "movie.mkv", kind="video", mime="video/x-matroska")) == "indexed"


async def test_probe_top_id_without_history_api(app):
    c = FakeClient(build(250) if False else [make_msg(-100, i, f"A.{i}.2010.mkv") for i in range(1, 231)])
    app.indexer.get_client = lambda: c
    top = await app.indexer.probe_top_id(-100)
    assert top == 230


async def test_add_source_requires_access(app):
    class NoAccess(FakeClient):
        async def get_messages(self, chat_id, ids):
            raise RuntimeError("denied")
    app.indexer.get_client = lambda: NoAccess([])
    with pytest.raises(Exception):
        await app.indexer.add_source(-100)
    app.indexer.get_client = lambda: FakeClient([])
    s = await app.indexer.add_source(-100, 1)
    assert s["_id"] == -100 and -100 in app.indexer.live_chats
