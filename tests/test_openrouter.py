import pytest
from ajcine.ai.openrouter import OpenRouterClient, AIUnavailable, extract_json
from ajcine.ai.identify import validate_identification, validate_query


class Resp:
    def __init__(self, status, payload, headers=None):
        self.status, self._p, self.headers = status, payload, headers or {}

    async def json(self, content_type=None):
        return self._p

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    closed = False

    def __init__(self, script, models=None):
        self.script = script              # {model: [Resp, ...]}
        self.calls = []
        self.models = models if models is not None else [
            {"id": "vendor/model-a:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/model-b:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "vendor/paid-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
            {"id": "vendor/sneaky:free", "pricing": {"prompt": "0", "completion": "0.000001"}},
        ]

    def get(self, url, headers=None):
        return Resp(200, {"data": self.models})

    def post(self, url, headers=None, json=None):
        self.calls.append(json["model"])
        q = self.script.get(json["model"], [])
        return q.pop(0) if q else Resp(500, {})


def ok(content='{"title": "Inception", "year": 2010, "content_type": "movie", "confidence": 0.9}'):
    return Resp(200, {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 42}})


async def sleepless(_):
    return None


def client(app, settings, script, **kw):
    sess = FakeSession(script, kw.get("models"))
    c = OpenRouterClient(settings, app.db, app.cache, session=sess, sleep=sleepless)
    return c, sess


async def test_uses_primary_free_model(app, settings):
    c, s = client(app, settings, {"vendor/model-a:free": [ok()]})
    data, model = await c.complete_json("t", "sys", "usr", {"x": 1})
    assert model == "vendor/model-a:free" and data["title"] == "Inception" and s.calls == ["vendor/model-a:free"]


async def test_result_is_cached_and_not_requested_twice(app, settings):
    c, s = client(app, settings, {"vendor/model-a:free": [ok()]})
    await c.complete_json("t", "sys", "usr", {"x": 1})
    data, _ = await c.complete_json("t", "sys", "usr", {"x": 1})
    assert data["title"] == "Inception" and len(s.calls) == 1


async def test_falls_back_when_primary_is_down(app, settings):
    c, s = client(app, settings, {"vendor/model-a:free": [Resp(503, {})] * 3, "vendor/model-b:free": [ok()]})
    _, model = await c.complete_json("t", "sys", "usr", {"y": 1})
    assert model == "vendor/model-b:free"


async def test_retries_429_then_succeeds(app, settings):
    c, s = client(app, settings, {"vendor/model-a:free": [Resp(429, {"error": {"message": "slow down"}}, {"Retry-After": "1"}), ok()]})
    _, model = await c.complete_json("t", "sys", "usr", {"z": 1})
    assert model == "vendor/model-a:free" and s.calls.count("vendor/model-a:free") == 2


async def test_paid_model_is_never_called(app, settings):
    s2 = settings.with_overrides(openrouter_model="vendor/paid-model", openrouter_fallback_models=("vendor/sneaky:free",))
    c, s = client(app, s2, {})
    with pytest.raises(AIUnavailable):
        await c.complete_json("t", "sys", "usr", {"p": 1})
    assert s.calls == []   # neither the paid model nor the ':free' model with a non-zero completion price


async def test_set_config_rejects_paid_models(app, settings):
    c, _ = client(app, settings, {})
    with pytest.raises(ValueError):
        await c.set_config(model="vendor/paid-model")
    with pytest.raises(ValueError):
        await c.set_config(fallbacks=["vendor/model-b:free", "vendor/paid-model"])
    cfg = await c.set_config(model="vendor/model-b:free")
    assert cfg["model"] == "vendor/model-b:free"


async def test_free_suffix_fallback_when_catalogue_unreachable(app, settings):
    c, _ = client(app, settings, {})
    c._catalog_ok = False
    c._catalog_at = 1e18
    assert c.is_free("any/model:free") and not c.is_free("any/model")


async def test_402_halts_and_never_retries(app, settings):
    c, s = client(app, settings, {"vendor/model-a:free": [Resp(402, {"error": {"message": "credits"}})]})
    with pytest.raises(AIUnavailable):
        await c.complete_json("t", "sys", "usr", {"c": 1})
    with pytest.raises(AIUnavailable):
        await c.complete_json("t", "sys", "usr", {"c": 2})
    assert len(s.calls) == 1


async def test_ai_can_be_disabled_without_error_paths(app, settings):
    c, s = client(app, settings, {})
    await c.set_config(enabled=False)
    assert not await c.enabled()
    with pytest.raises(AIUnavailable):
        await c.complete_json("t", "sys", "usr", {"d": 1})
    assert s.calls == []


async def test_missing_key_means_unavailable(app, settings):
    c, _ = client(app, settings.with_overrides(openrouter_api_key=""), {})
    with pytest.raises(AIUnavailable):
        await c.complete_json("t", "s", "u", {"k": 1})


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(Exception):
        extract_json("no json here")


def test_validation_rejects_invented_ids_and_bad_values():
    r = validate_identification({"title": "Inception", "year": 3000, "imdb_id": "tt1375666", "rating": 9.9,
                                 "content_type": "film", "languages": ["Malayalam", "Klingon"], "resolution": "1080P",
                                 "season": -1, "confidence": 7})
    assert r.title == "Inception" and r.year is None and r.content_type is None
    assert r.languages == ["ml"] and r.resolution == "1080p" and r.season is None and r.confidence == 1.0
    assert not hasattr(r, "imdb_id") and not hasattr(r, "rating")
    assert validate_identification({"title": "http://evil.example"}) is None
    assert validate_identification({"title": None}) is None
    assert validate_query({"title": "Premalu", "language": "ml", "year": 2024}).language == "ml"
