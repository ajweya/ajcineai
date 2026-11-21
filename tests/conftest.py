"""Shared fixtures: in-memory Mongo, fake Redis, stub title lookup, fake Telegram."""
from __future__ import annotations

import types

import pytest
import fakeredis.aioredis
from mongomock_motor import AsyncMongoMockClient

from ajcine.app import build_app
from ajcine.cache import Cache
from ajcine.config import Settings
from ajcine.matching.models import Candidate, MatchQuery, rank_candidates

CATALOG = [
    Candidate("tt0816692", "Interstellar", 2014, "movie", genres=["Sci-Fi"], rating=8.7, votes=2000000),
    Candidate("tt1375666", "Inception", 2010, "movie", genres=["Action", "Sci-Fi"], rating=8.8, votes=2500000, plot="A thief enters dreams."),
    Candidate("tt0903747", "Breaking Bad", 2008, "series", genres=["Crime", "Drama"], rating=9.5, votes=2000000),
    Candidate("tt26581740", "Premalu", 2024, "movie", genres=["Comedy", "Romance"], rating=7.6, votes=30000),
    Candidate("tt0133093", "The Matrix", 1999, "movie", rating=8.7, votes=2000000),
    Candidate("tt0234215", "The Matrix Reloaded", 2003, "movie", rating=7.2, votes=600000),
    Candidate("tt0386676", "The Office", 2005, "series", rating=9.0, votes=700000),
    Candidate("tt0290978", "The Office", 2001, "series", rating=8.5, votes=90000),
]


class FakeTitles:
    """Stands in for the TMDB-backed TitleService."""

    enabled = True

    def __init__(self, catalog=None):
        self.catalog = list(catalog if catalog is not None else CATALOG)
        self.searches = 0
        self.down = False

    async def search(self, q: MatchQuery, limit=12, detail_top=5):
        from ajcine.matching.service import LookupUnavailable
        if self.down:
            raise LookupUnavailable("down")
        self.searches += 1
        import copy
        return rank_candidates(q, [copy.deepcopy(c) for c in self.catalog])[:limit]

    async def get(self, imdb_id):
        import copy
        return next((copy.deepcopy(c) for c in self.catalog if c.imdb_id == imdb_id), None)

    async def episode(self, imdb_id, season, episode):
        return {"title": f"Episode {episode}"} if imdb_id == "tt0903747" else None

    async def probe(self):
        return {"ok": True}

    async def reload(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def settings():
    return Settings(api_id=1, api_hash="x", bot_token="1:x", owner_ids=(111,), web_secret_key="k" * 40,
                    admin_bootstrap_password="correct-horse-battery", openrouter_api_key="sk-test",
                    openrouter_model="vendor/model-a:free", openrouter_fallback_models=("vendor/model-b:free",),
                    poster_dir="/tmp/ajcine-test-posters", backup_dir="/tmp/ajcine-test-backups",
                    ai_backoff_base=0.0, index_batch_delay=0.0)


@pytest.fixture
async def app(settings):
    db = AsyncMongoMockClient()["ajtest"]
    cache = Cache(client=fakeredis.aioredis.FakeRedis(decode_responses=True))
    a = await build_app(settings, db=db, cache=cache)
    fake = FakeTitles()
    a.lookup = fake
    a.pipeline.lookup = fake
    a.review.lookup = fake
    yield a


def make_msg(chat_id, mid, name, size=1_500_000_000, unique=None, kind="document", caption=None, mime="video/x-matroska"):
    media = types.SimpleNamespace(file_id=f"FID{chat_id}_{mid}", file_unique_id=unique or f"U{chat_id}_{mid}",
                                  file_name=name, file_size=size, mime_type=mime, duration=7000)
    ns = dict(id=mid, chat=types.SimpleNamespace(id=chat_id), empty=False, service=None, caption=caption,
              date=None, video=None, document=None, audio=None)
    ns[kind] = media
    return types.SimpleNamespace(**ns)
