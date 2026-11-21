"""IMDb candidate model and the weighted matching engine.

The engine never trusts a single signal. A title is only auto-approved when
the text similarity is strong AND the year/type agree AND no equally good
rival exists. Everything else is routed to the human review queue.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from rapidfuzz import fuzz

from ..parsing.text import match_key, phonetic_key

SERIES_KINDS = {"tv series", "tv mini series", "tv mini-series", "series"}
MOVIE_KINDS = {"movie", "tv movie", "video movie", "tv special", "documentary", "film"}

# weights (sum to 1.0)
W_TITLE, W_YEAR, W_TYPE, W_ORIG, W_META = 0.55, 0.20, 0.10, 0.05, 0.10


def kind_of(raw_kind: str | None) -> str | None:
    k = (raw_kind or "").lower().strip()
    if k in SERIES_KINDS:
        return "series"
    if k in MOVIE_KINDS:
        return "movie"
    return None  # episodes, games, shorts, podcasts... are not catalog titles


@dataclass
class Candidate:
    imdb_id: str
    title: str
    year: int | None = None
    kind: str = "movie"  # movie | series
    original_title: str | None = None
    alt_titles: list[str] = field(default_factory=list)
    rating: float | None = None
    votes: int | None = None
    genres: list[str] = field(default_factory=list)
    runtime: int | None = None
    plot: str | None = None
    score: float = 0.0
    title_sim: float = 0.0
    reasons: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class MatchQuery:
    title: str
    year: int | None = None
    kind: str | None = None  # movie | series | None
    alt_titles: list[str] = field(default_factory=list)


def similarity(a: str | None, b: str | None) -> float:
    ka, kb = match_key(a), match_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    best = max(fuzz.ratio(ka, kb), fuzz.token_sort_ratio(ka, kb)) / 100.0
    pa, pb = phonetic_key(a), phonetic_key(b)
    if pa and pb:
        best = max(best, fuzz.ratio(pa, pb) / 100.0 * 0.97)
    # "Dune" vs "Dune: Part Two": one is a whole-word prefix of the other
    if kb.startswith(ka + " ") or ka.startswith(kb + " "):
        best = max(best, 0.82)
    return best


def _year_score(qy: int | None, cy: int | None) -> tuple[float, bool]:
    """Return (score, hard_mismatch)."""
    if qy is None:
        return 0.5, False
    if cy is None:
        return 0.35, False
    diff = abs(qy - cy)
    if diff == 0:
        return 1.0, False
    if diff == 1:
        return 0.75, False
    return 0.0, True


def _meta_score(c: Candidate) -> float:
    s = 0.0
    s += 0.4 if c.rating else 0.0
    s += 0.3 if c.genres else 0.0
    if c.votes:
        s += 0.3 * min(1.0, math.log10(max(c.votes, 1)) / 5.0)
    return min(s, 1.0)


def score_candidate(q: MatchQuery, c: Candidate) -> Candidate:
    names_q = [q.title] + list(q.alt_titles)
    names_c = [c.title] + list(c.alt_titles)
    tsim = max((similarity(a, b) for a in names_q for b in names_c if a and b), default=0.0)
    orig = similarity(q.title, c.original_title) if c.original_title else tsim
    ysc, year_bad = _year_score(q.year, c.year)

    type_bad = False
    if q.kind is None:
        tsc = 0.7
    elif q.kind == c.kind:
        tsc = 1.0
    else:
        tsc, type_bad = 0.0, True

    total = W_TITLE * tsim + W_YEAR * ysc + W_TYPE * tsc + W_ORIG * orig + W_META * _meta_score(c)
    caps = []
    if tsim < 0.6:
        total, _ = min(total, 0.5), caps.append("title<0.6")
    if year_bad:
        total, _ = min(total, 0.70), caps.append("year-mismatch")
    if type_bad:
        total, _ = min(total, 0.60), caps.append("type-mismatch")
    c.score = round(total, 4)
    c.title_sim = round(tsim, 4)
    c.reasons = {
        "title": round(tsim, 3), "year": round(ysc, 3), "type": round(tsc, 3),
        "original": round(orig, 3), "meta": round(_meta_score(c), 3), "caps": caps,
    }
    return c


def rank_candidates(q: MatchQuery, cands: list[Candidate]) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for c in cands:  # duplicate identification: one entry per IMDb id
        if c.imdb_id not in seen:
            seen[c.imdb_id] = c
    scored = [score_candidate(q, c) for c in seen.values()]
    scored.sort(key=lambda c: (c.score, c.votes or 0), reverse=True)
    return scored


def decide(ranked: list[Candidate], auto: float, review: float) -> tuple[str, Candidate | None, str]:
    """Return (status, best, why) where status is auto | review | none."""
    if not ranked:
        return "none", None, "no candidates"
    best = ranked[0]
    if best.score < review:
        return "none", best, f"best score {best.score:.2f} below review threshold"
    rival = next((c for c in ranked[1:] if c.imdb_id != best.imdb_id), None)
    if best.score >= auto:
        if rival and rival.title_sim >= 0.9 and (best.score - rival.score) < 0.04:
            dominant = (best.votes or 0) >= 10 * max(rival.votes or 0, 1)
            if not dominant:
                return "review", best, f"ambiguous with {rival.imdb_id}"
        return "auto", best, "confident match"
    return "review", best, f"score {best.score:.2f} below auto threshold"
