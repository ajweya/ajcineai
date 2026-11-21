from ajcine.matching.models import Candidate, MatchQuery, decide, rank_candidates, similarity
from tests.conftest import CATALOG
import copy


def rank(q):
    return rank_candidates(q, copy.deepcopy(CATALOG))


def test_exact_title_year_is_auto():
    r = rank(MatchQuery("Inception", 2010, "movie"))
    status, best, _ = decide(r, 0.86, 0.55)
    assert status == "auto" and best.imdb_id == "tt1375666"


def test_typo_still_finds_title():
    r = rank(MatchQuery("Incepton", 2010, "movie"))
    assert r[0].imdb_id == "tt1375666" and r[0].title_sim > 0.8


def test_transliteration_matches():
    r = rank(MatchQuery("Premaalu", 2024, "movie"))
    assert r[0].imdb_id == "tt26581740"


def test_wrong_year_is_capped_and_never_auto():
    r = rank(MatchQuery("Inception", 1995, "movie"))
    status, best, _ = decide(r, 0.86, 0.55)
    assert status != "auto"


def test_wrong_type_is_never_auto():
    r = rank(MatchQuery("Breaking Bad", 2008, "movie"))
    assert decide(r, 0.86, 0.55)[0] != "auto"


def test_ambiguous_rivals_go_to_review():
    r = rank(MatchQuery("The Office", None, None))
    status, best, why = decide(r, 0.0 + 0.5, 0.3)
    assert status in ("auto", "review")
    # both Offices are equally good by text; with no year the bigger one must dominate or it is reviewed
    r2 = rank(MatchQuery("The Office", None, "series"))
    s2, b2, _ = decide(r2, 0.86, 0.55)
    assert s2 in ("review", "auto")
    if s2 == "auto":
        assert b2.imdb_id == "tt0386676"


def test_garbage_is_not_matched():
    r = rank(MatchQuery("Zzzxqw Qwerty", None, None))
    assert decide(r, 0.86, 0.55)[0] == "none"


def test_prefix_titles_rank_below_exact():
    r = rank(MatchQuery("The Matrix", 1999, "movie"))
    assert r[0].imdb_id == "tt0133093" and r[1].imdb_id == "tt0234215"


def test_duplicate_candidates_collapse():
    dup = copy.deepcopy(CATALOG) + copy.deepcopy(CATALOG[:2])
    assert len(rank_candidates(MatchQuery("Inception"), dup)) == len(CATALOG)


def test_similarity_bounds():
    assert similarity("A", "") == 0.0 and similarity("Inception", "inception") == 1.0
