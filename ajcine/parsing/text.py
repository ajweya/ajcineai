
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")
_ARTICLES = ("the ", "a ", "an ")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def norm(text: str | None) -> str:
    """Lowercase, accent-free, punctuation-free, single-spaced."""
    if not text:
        return ""
    t = strip_accents(text).lower().replace("&", " and ")
    t = t.replace("_", " ")
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def match_key(text: str | None) -> str:
    """Normalized key that also ignores a leading article."""
    t = norm(text)
    for art in _ARTICLES:
        if t.startswith(art) and len(t) > len(art) + 1:
            return t[len(art):]
    return t


_PHONETIC_STEPS = (
    ("ch", "\x01"), ("zh", "l"), ("ph", "f"), ("th", "t"), ("dh", "d"),
    ("kh", "k"), ("gh", "g"), ("bh", "b"), ("sh", "s"), ("ck", "k"),
    ("q", "k"), ("w", "v"), ("x", "ks"), ("c", "k"), ("\x01", "c"),
    ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"), ("aa", "a"),
    ("ou", "u"), ("y", "i"),
)


def phonetic_key(text: str | None) -> str:
    """Rough Indic-friendly phonetic key so 'Premaalu' == 'Premalu'.

    Cheap and deterministic: used as an extra similarity signal, never as the
    sole basis for an identification.
    """
    t = match_key(text)
    if not t:
        return ""
    for a, b in _PHONETIC_STEPS:
        t = t.replace(a, b)
    out: list[str] = []
    for ch in t:  # collapse doubled letters
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def tokens_for_index(*texts: str | None, gram: int = 3) -> list[str]:
    """Search tokens: words, phonetic words and character trigrams."""
    toks: set[str] = set()
    for text in texts:
        n = norm(text)
        if not n:
            continue
        for w in n.split():
            toks.add(w)
            p = phonetic_key(w)
            if p:
                toks.add("~" + p)
        flat = "_" + n.replace(" ", "_") + "_"
        for i in range(max(1, len(flat) - gram + 1)):
            toks.add("#" + flat[i:i + gram])
    return sorted(toks)


def query_tokens(text: str) -> list[str]:
    return tokens_for_index(text)
