"""AI-assisted normalization with strict validation.

The model only *normalizes text* (title, year, season...). It is never trusted
for IMDb ids, ratings or any catalogue fact: those fields are rejected, and
every AI title must still be confirmed by a TMDB lookup.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..parsing.languages import lang_code
from ..parsing.parser import ParsedName
from .openrouter import AIError, OpenRouterClient

log = logging.getLogger("ajcine.ai.identify")

_RES = {"2160p", "1440p", "1080p", "720p", "576p", "540p", "480p", "360p", "240p"}
_FORBIDDEN_FIELDS = ("imdb_id", "imdb", "rating", "votes", "poster", "url")

SYSTEM_IDENTIFY = (
    "You normalise messy movie and TV release filenames into structured JSON. "
    "Reply with ONLY one JSON object and nothing else. "
    "Rules: never invent facts; use null when unsure; do not output IMDb ids, ratings or URLs. "
    "Fix obvious misspellings of well known titles. Titles must be the official title without release tags."
)
USER_IDENTIFY = """Filename: {filename}
Caption: {caption}
Deterministic parser hints (may be wrong): {hints}

Return JSON with exactly these keys:
{{"title": str|null, "original_title": str|null, "alt_titles": [str], "year": int|null,
"content_type": "movie"|"series"|null, "season": int|null, "episode": int|null,
"episode_title": str|null, "languages": [str], "resolution": str|null,
"video_codec": str|null, "audio_codec": str|null, "hdr": [str], "edition": [str],
"confidence": number between 0 and 1}}
Languages are full English names such as "Malayalam" or "Hindi"."""

SYSTEM_QUERY = (
    "You interpret a user's search for a movie or TV series. The query may be misspelled, Manglish "
    "(Malayalam in Latin letters), transliterated from another language, or written in another script. "
    "Reply with ONLY one JSON object. Never invent facts; use null when unsure."
)
USER_QUERY = """Search query: {query}

Return JSON: {{"title": str|null, "alt_titles": [str], "year": int|null,
"content_type": "movie"|"series"|null, "language": str|null}}
"title" must be the most likely official (usually English or original Latin-script) title."""


@dataclass
class AIResult:
    title: str
    original_title: str | None = None
    alt_titles: list[str] = field(default_factory=list)
    year: int | None = None
    content_type: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    languages: list[str] = field(default_factory=list)  # codes
    resolution: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    hdr: list[str] = field(default_factory=list)
    edition: list[str] = field(default_factory=list)
    confidence: float = 0.0
    model: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class QueryInterpretation:
    title: str
    alt_titles: list[str] = field(default_factory=list)
    year: int | None = None
    content_type: str | None = None
    language: str | None = None  # code
    model: str | None = None


def _s(v, n=200) -> str | None:
    if not isinstance(v, str):
        return None
    v = re.sub(r"\s+", " ", v).strip()
    if not v or len(v) > n or re.search(r"https?://|www\.", v, re.I):
        return None
    return v


def _i(v, lo, hi) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _list(v, n=8) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for x in v[:n]:
        s = _s(x, 120)
        if s and s not in out:
            out.append(s)
    return out


def validate_identification(data: dict, model: str | None = None) -> AIResult | None:
    """Strict validation. Returns None when the payload is unusable."""
    if not isinstance(data, dict):
        return None
    title = _s(data.get("title"))
    if not title:
        return None
    max_year = datetime.now(timezone.utc).year + 2
    ctype = data.get("content_type")
    ctype = ctype if ctype in ("movie", "series") else None
    langs = []
    for name in _list(data.get("languages"), 8):
        code = lang_code(name)
        if code and code not in langs:
            langs.append(code)
    res = data.get("resolution")
    res = res.lower() if isinstance(res, str) and res.lower() in _RES else None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return AIResult(
        title=title,
        original_title=_s(data.get("original_title")),
        alt_titles=_list(data.get("alt_titles")),
        year=_i(data.get("year"), 1888, max_year),
        content_type=ctype,
        season=_i(data.get("season"), 0, 99),
        episode=_i(data.get("episode"), 0, 999),
        episode_title=_s(data.get("episode_title")),
        languages=langs,
        resolution=res,
        video_codec=_s(data.get("video_codec"), 30),
        audio_codec=_s(data.get("audio_codec"), 30),
        hdr=_list(data.get("hdr"), 4),
        edition=_list(data.get("edition"), 4),
        confidence=conf,
        model=model,
    )  # forbidden fields (imdb_id, rating, ...) are simply never read


def validate_query(data: dict, model: str | None = None) -> QueryInterpretation | None:
    if not isinstance(data, dict):
        return None
    title = _s(data.get("title"))
    if not title:
        return None
    ctype = data.get("content_type")
    return QueryInterpretation(
        title=title,
        alt_titles=_list(data.get("alt_titles"), 5),
        year=_i(data.get("year"), 1888, datetime.now(timezone.utc).year + 2),
        content_type=ctype if ctype in ("movie", "series") else None,
        language=lang_code(data.get("language")) if isinstance(data.get("language"), str) else None,
        model=model,
    )


class AIIdentifier:
    def __init__(self, client: OpenRouterClient):
        self.client = client

    async def available(self) -> bool:
        return await self.client.enabled()

    async def normalise(self, filename: str, caption: str | None, parsed: ParsedName,
                        max_wait: float = 60.0) -> AIResult | None:
        hints = {k: v for k, v in {
            "title": parsed.title, "year": parsed.year, "type": parsed.content_type,
            "season": parsed.season, "episode": parsed.episode, "languages": parsed.language_names,
        }.items() if v not in (None, "", [])}
        payload = {"f": filename, "c": (caption or "")[:300]}
        user = USER_IDENTIFY.format(filename=json.dumps(filename), caption=json.dumps((caption or "")[:300]),
                                    hints=json.dumps(hints))
        try:
            data, model = await self.client.complete_json(
                "identify", SYSTEM_IDENTIFY, user, payload, max_wait=max_wait)
        except AIError as exc:
            log.info("AI identify skipped: %s", exc)
            return None
        res = validate_identification(data, model)
        if res is None:
            log.info("AI identify returned an invalid payload from %s", model)
        return res

    async def interpret_query(self, query: str, max_wait: float = 6.0) -> QueryInterpretation | None:
        q = re.sub(r"\s+", " ", query).strip()[:120]
        if not q:
            return None
        try:
            data, model = await self.client.complete_json(
                "query", SYSTEM_QUERY, USER_QUERY.format(query=json.dumps(q)), {"q": q.lower()},
                max_wait=max_wait, max_tokens=250)
        except AIError as exc:
            log.info("AI query interpretation skipped: %s", exc)
            return None
        return validate_query(data, model)
