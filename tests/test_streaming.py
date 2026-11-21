import asyncio
import types

import pytest

from ajcine.streaming import (CHUNK, MediaResolver, RangeError, SourceGone, StreamBusy, StreamLimiter, browser_hint,
                              content_disposition, iter_range, parse_range)

SIZE = 3 * CHUNK + 12345
DATA = bytes((i * 7 + i // 251) % 256 for i in range(SIZE))


class FakeStreamClient:
    """Mimics pyrofork's stream_media(limit, offset) in 1 MiB chunks."""

    def __init__(self, data=DATA, fail_after=None):
        self.data, self.calls, self.fail_after, self.closed = data, [], fail_after, 0

    async def stream_media(self, message, limit=0, offset=0):
        self.calls.append((limit, offset))
        n = 0
        try:
            for i in range(offset, (len(self.data) + CHUNK - 1) // CHUNK):
                if limit and n >= limit:
                    return
                if self.fail_after is not None and n >= self.fail_after:
                    raise RuntimeError("boom")
                n += 1
                yield self.data[i * CHUNK:(i + 1) * CHUNK]
                await asyncio.sleep(0)
        finally:
            self.closed += 1


async def collect(client, start, end):
    return b"".join([c async for c in iter_range(client, object(), start, end)])


# ---- range parsing --------------------------------------------------------------------------
@pytest.mark.parametrize("hdr,size,exp", [
    (None, 100, None), ("", 100, None), ("bytes=0-", 100, (0, 99)), ("bytes=10-19", 100, (10, 19)),
    ("bytes=90-500", 100, (90, 99)), ("bytes=-10", 100, (90, 99)), ("bytes=-500", 100, (0, 99)),
    ("bytes=0-0", 100, (0, 0)), ("BYTES = 5 - 6", 100, (5, 6)), ("bytes=0-10,20-30", 100, (0, 10)),
    ("items=0-5", 100, None), ("bytes=abc", 100, None), ("bytes=-", 100, None), ("bytes=20-10", 100, None),
])
def test_parse_range_valid_and_ignored(hdr, size, exp):
    assert parse_range(hdr, size) == exp


@pytest.mark.parametrize("hdr", ["bytes=100-", "bytes=150-200", "bytes=-0"])
def test_parse_range_unsatisfiable(hdr):
    with pytest.raises(RangeError):
        parse_range(hdr, 100)


# ---- ranged reads ----------------------------------------------------------------------------
@pytest.mark.parametrize("start,end", [
    (0, SIZE - 1), (0, 0), (5, 5), (CHUNK - 1, CHUNK), (CHUNK, 2 * CHUNK - 1), (123, CHUNK * 2 + 77),
    (SIZE - 10, SIZE - 1), (2 * CHUNK + 5, SIZE - 1), (1, SIZE - 2),
])
async def test_iter_range_returns_exactly_the_requested_bytes(start, end):
    assert await collect(FakeStreamClient(), start, end) == DATA[start:end + 1]


async def test_iter_range_asks_telegram_only_for_needed_chunks():
    c = FakeStreamClient()
    await collect(c, CHUNK + 10, CHUNK + 20)
    assert c.calls == [(1, 1)]
    c = FakeStreamClient()
    await collect(c, CHUNK - 1, CHUNK)
    assert c.calls == [(2, 0)]


async def test_iter_range_stops_reader_when_consumer_leaves():
    c = FakeStreamClient()
    gen = iter_range(c, object(), 0, SIZE - 1)
    first = await gen.__anext__()
    assert first == DATA[:CHUNK]
    await gen.aclose()
    await asyncio.sleep(0.01)
    assert c.closed == 1


async def test_iter_range_surfaces_telegram_errors():
    c = FakeStreamClient(fail_after=1)
    with pytest.raises(RuntimeError):
        await collect(c, 0, SIZE - 1)


# ---- limiter ------------------------------------------------------------------------------------
def test_limiter_global_and_per_ip():
    lim = StreamLimiter(3, 2)
    lim.acquire("a"); lim.acquire("a")
    with pytest.raises(StreamBusy):
        lim.acquire("a")
    lim.acquire("b")
    with pytest.raises(StreamBusy):
        lim.acquire("c")
    lim.release("a")
    lim.acquire("c")
    for ip in ("a", "b", "c"):
        lim.release(ip)
    lim.release("zzz")
    assert lim.total >= 0


# ---- resolver --------------------------------------------------------------------------------------
class ResolverClient:
    def __init__(self, msgs=None, err=None):
        self.msgs, self.err, self.n = msgs or {}, err, 0

    async def get_messages(self, chat, mid):
        self.n += 1
        if self.err:
            raise self.err
        return self.msgs.get((chat, mid))


def media_msg(size=1000):
    return types.SimpleNamespace(empty=False, video=None, audio=None,
                                 document=types.SimpleNamespace(file_size=size, mime_type="video/x-matroska"))


FILE = {"_id": "-1:1", "source_chat_id": -1, "source_message_id": 1, "alt_sources": [{"chat_id": -2, "message_id": 9}]}


async def test_resolver_finds_primary_and_caches():
    c = ResolverClient({(-1, 1): media_msg(555)})
    r = MediaResolver(lambda: [c])
    res = await r.resolve(FILE)
    assert res.size == 555 and res.client is c
    await r.resolve(FILE)
    assert c.n == 1


async def test_resolver_falls_back_to_duplicate_source():
    c = ResolverClient({(-2, 9): media_msg(777)})
    res = await MediaResolver(lambda: [c]).resolve(FILE)
    assert res.size == 777 and (res.chat_id, res.message_id) == (-2, 9)


async def test_resolver_reports_gone_and_busy():
    with pytest.raises(SourceGone):
        await MediaResolver(lambda: [ResolverClient()]).resolve(FILE)

    class FloodWait(Exception):
        pass
    with pytest.raises(StreamBusy):
        await MediaResolver(lambda: [ResolverClient(err=FloodWait())]).resolve(FILE)


# ---- hints and headers ----------------------------------------------------------------------------
def test_browser_hint_levels():
    assert browser_hint({"file_name": "a.mp4"}) == "good"
    assert browser_hint({"file_name": "a.mkv"}) == "maybe"
    assert browser_hint({"file_name": "a.mp4", "video_codec": "HEVC"}) == "maybe"
    assert browser_hint({"file_name": "a.avi"}) == "poor"
    assert browser_hint({"file_name": "a.mp4", "audio_codec": "DTS"}) == "maybe"


def test_content_disposition_is_header_safe():
    h = content_disposition('Bad"\r\nName ü.mkv')
    assert "\r" not in h and "\n" not in h and h.count('"') == 2 and "filename*=UTF-8''" in h
