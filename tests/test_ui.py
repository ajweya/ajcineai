from ajcine.bot import ui
from ajcine.bot.admin import CB_RE


def test_valid_callbacks_parse():
    assert ui.parse_cb("t:tt1375666")[0] == "t"
    assert ui.parse_cb("q:tt1375666:ml:1080p")[1] == ("tt1375666", "ml", "1080p")
    assert ui.parse_cb("eq:tt0903747:2:5:en:2160p")[1] == ("tt0903747", "2", "5", "en", "2160p")
    assert ui.parse_cb("pq:tt0903747:s:2:en:1080p")[0] == "pq"
    assert ui.parse_cb("t:loc_0123456789")[0] == "t"
    assert ui.parse_cb("p:3:abcdef123456")[0] == "p"


import pytest


@pytest.mark.parametrize("bad", [None, "", "t:", "t:tt1", "t:tt1375666;drop", "q:tt1375666:ML:1080p", "zz:tt1375666",
                                 "e:tt1375666:999:1", "t:" + "t" * 80, "a:home", "n:tt1375666:1; --", "l:tt1375666:ml:x"])
def test_invalid_callbacks_are_rejected(bad):
    assert ui.parse_cb(bad) is None


def test_worst_case_callbacks_fit_telegram_limit():
    worst = ["eq:tt12345678901:99:9999:xx:2160p", "pq:tt12345678901:s:99:xx:2160p", "el:tt12345678901:99:9999:xx",
             "a:rvc:4:-1001234567890123:123456789"]
    assert all(len(w.encode()) <= 64 for w in worst)


def test_admin_callback_pattern():
    assert CB_RE.match("a:srcv:1001234567890")
    assert CB_RE.match("a:dash") and not CB_RE.match("a:dash;rm") and not CB_RE.match("x:dash")


def test_title_card_uses_only_real_data():
    doc = {"_id": "tt1", "title": "A <b>", "year": 2020, "kind": "movie", "genres": ["Drama"], "languages": ["ml", "en"]}
    t = ui.title_card(doc)
    assert "A &lt;b&gt;" in t and "Malayalam" in t and "⭐" not in t           # no rating => no rating line
    loc = {"_id": "loc_0123456789", "title": "X", "rating": 9.9, "kind": "series"}
    assert "9.9" not in ui.title_card(loc)                                       # local titles never show IMDb ratings


def test_results_keyboard_pagination():
    res = {"items": [{"id": "tt1375666", "title": "Inception", "year": 2010, "kind": "movie", "rating": 8.8}], "page": 2, "pages": 3}
    rows = ui.results_keyboard(res, "tok123456789")
    assert rows[0][0][1] == "t:tt1375666" and [b[0] for b in rows[1]] == ["Prev", "Next"]
