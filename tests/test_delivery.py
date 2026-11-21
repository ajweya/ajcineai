import types
from datetime import timedelta
import pytest
from ajcine import db as dbm
from ajcine.catalog import now
from ajcine.delivery import DeliveryError, _h
from tests.conftest import make_msg


class FakeTG:
    def __init__(self, gone=()):
        self.sent, self.gone = [], set(gone)

    async def copy_message(self, to, from_chat, mid, caption=None, protect_content=False):
        if (from_chat, mid) in self.gone:
            class MessageIdInvalid(Exception):
                pass
            raise MessageIdInvalid()
        self.sent.append((to, from_chat, mid, caption))
        return types.SimpleNamespace(id=len(self.sent))

    async def send_cached_media(self, *a, **k):
        raise RuntimeError("no")


async def setup_file(app, name="Inception.2010.English.1080p.mkv", mid=1):
    await app.indexer.ingest_message(make_msg(-100, mid, name, size=2_000_000_000, unique=f"U{mid}"))
    return f"-100:{mid}"


async def test_correct_indexed_file_is_delivered_to_the_requester(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid], "tt1375666")
    tg = FakeTG()
    rep = await app.delivery.deliver(tg, tok, 42)
    assert rep["sent"] == 1 and tg.sent[0][:3] == (42, -100, 1)
    assert "Inception" in tg.sent[0][3] and "1080p" in tg.sent[0][3] and "GB" in tg.sent[0][3]
    assert await app.db[dbm.DELIVERIES].count_documents({"user_id": 42, "status": "delivered"}) == 1


async def test_token_is_user_bound(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid], "tt1375666")
    with pytest.raises(DeliveryError) as e:
        await app.delivery.deliver(FakeTG(), tok, 43)
    assert e.value.code == "forbidden"


async def test_token_expires(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid])
    await app.db[dbm.DELIVERY_TOKENS].update_one({"_id": _h(tok)}, {"$set": {"expires_at": now() - timedelta(seconds=1)}})
    with pytest.raises(DeliveryError) as e:
        await app.delivery.resolve_token(tok, 42)
    # real MongoDB removes the doc up to 60 s late (-> "expired"); the in-memory test DB removes it at once (-> "invalid")
    assert e.value.code in ("expired", "invalid")


async def test_token_use_limit_and_garbage(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid])
    for _ in range(app.settings.token_max_uses):
        await app.delivery.deliver(FakeTG(), tok, 42)
    with pytest.raises(DeliveryError) as e:
        await app.delivery.deliver(FakeTG(), tok, 42)
    assert e.value.code == "used"
    for bad in ("", "x" * 200, "../../etc", "a b", "nonexistenttoken123"):
        with pytest.raises(DeliveryError):
            await app.delivery.resolve_token(bad, 42)


async def test_tokens_are_stored_hashed(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid])
    assert await app.db[dbm.DELIVERY_TOKENS].find_one({"_id": tok}) is None
    assert await app.db[dbm.DELIVERY_TOKENS].find_one({"_id": _h(tok)})


async def test_banned_and_restricted_users_cannot_download(app):
    fid = await setup_file(app)
    await app.users.touch(types.SimpleNamespace(id=42, first_name="a", username="a"))
    tok = await app.delivery.create_token(42, [fid])
    await app.users.set_permission(42, "download", False)
    with pytest.raises(DeliveryError) as e:
        await app.delivery.resolve_token(tok, 42)
    assert e.value.code == "restricted"
    await app.users.set_permission(42, "download", True)
    await app.users.ban(42)
    with pytest.raises(DeliveryError) as e:
        await app.delivery.resolve_token(tok, 42)
    assert e.value.code == "banned"


async def test_owner_cannot_be_banned(app):
    assert await app.users.ban(111) is False and not await app.users.is_banned(111)


async def test_deleted_source_message_is_detected_and_hidden(app):
    fid = await setup_file(app)
    tok = await app.delivery.create_token(42, [fid], "tt1375666")
    rep = await app.delivery.deliver(FakeTG(gone={(-100, 1)}), tok, 42)
    assert rep["sent"] == 0 and rep["missing"] == 1
    assert (await app.db[dbm.FILES].find_one({"_id": fid}))["missing"] is True
    assert await app.catalog.languages_for("tt1375666") == []            # no longer offered to users
    assert await app.db[dbm.DELIVERIES].count_documents({"status": "missing"}) == 1


async def test_alternate_source_is_used_when_primary_is_gone(app):
    fid = await setup_file(app)
    await app.indexer.ingest_message(make_msg(-200, 7, "Inception.2010.English.1080p.mkv", unique="U1"))   # same file elsewhere
    tok = await app.delivery.create_token(42, [fid], "tt1375666")
    tg = FakeTG(gone={(-100, 1)})
    rep = await app.delivery.deliver(tg, tok, 42)
    assert rep["sent"] == 1 and tg.sent[0][1:3] == (-200, 7)


async def test_multi_file_delivery_for_packs(app):
    ids = [await setup_file(app, f"Breaking.Bad.S01E0{i}.720p.mkv", i) for i in (1, 2, 3)]
    tok = await app.delivery.create_token(42, ids, "tt0903747")
    tg = FakeTG()
    assert (await app.delivery.deliver(tg, tok, 42))["sent"] == 3


async def test_token_creation_is_rate_limited(app):
    fid = await setup_file(app)
    with pytest.raises(DeliveryError) as e:
        for _ in range(app.settings.rate_limit_per_minute + 2):
            await app.delivery.create_token(42, [fid])
    assert e.value.code == "rate"


async def test_unidentified_files_are_never_delivered(app):
    await app.indexer.ingest_message(make_msg(-100, 5, "zzz.qqq.mkv", unique="Z"))
    tok = await app.delivery.create_token(42, ["-100:5"])
    rep = await app.delivery.deliver(FakeTG(), tok, 42)
    assert rep["sent"] == 0 and rep["missing"] == 1
