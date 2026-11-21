"""Language vocabulary and detection for release names and captions."""
from __future__ import annotations

import re

# canonical code -> display name
LANG_NAMES: dict[str, str] = {
    "en": "English", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil",
    "te": "Telugu", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "pa": "Punjabi", "gu": "Gujarati", "ur": "Urdu", "ko": "Korean",
    "ja": "Japanese", "zh": "Chinese", "es": "Spanish", "fr": "French",
    "de": "German", "ru": "Russian", "ar": "Arabic", "th": "Thai",
    "tr": "Turkish", "it": "Italian", "pt": "Portuguese", "fa": "Persian",
    "id": "Indonesian", "xx": "Unknown",
}
_NAME_TO_CODE = {v.lower(): k for k, v in LANG_NAMES.items() if k != "xx"}

# Full words are unambiguous enough to count on their own.
FULL_WORDS: dict[str, str] = {
    **_NAME_TO_CODE,
    "bangla": "bn", "mandarin": "zh", "cantonese": "zh", "farsi": "fa",
}
# Abbreviations only count inside a run of two or more language tokens.
ABBR_WORDS: dict[str, str] = {
    "eng": "en", "hin": "hi", "mal": "ml", "mlm": "ml", "malay": "ml",
    "tam": "ta", "tel": "te", "kan": "kn", "ben": "bn", "mar": "mr",
    "pun": "pa", "kor": "ko", "jap": "ja", "chi": "zh", "spa": "es",
    "fre": "fr", "ger": "de", "rus": "ru", "ara": "ar", "tha": "th",
    "tur": "tr", "ita": "it", "por": "pt", "guj": "gu", "urd": "ur",
}
# "Malay" is a real language but in this ecosystem it is overwhelmingly a typo
# of Malayalam; keep it as an abbreviation (run-only) so a lone "Malay" is ignored.

_ALL = {**FULL_WORDS, **ABBR_WORDS}
_TOKEN = r"[A-Za-z]{2,10}"
_SEP = r"[\s+,/&\-_.]+"

_SUB_BEFORE = re.compile(rf"(?<![A-Za-z])({_TOKEN})[\s._-]?(?:subs?|subtitles?)(?![A-Za-z])", re.I)
_SUB_AFTER = re.compile(rf"(?<![A-Za-z])(?:subs?|subtitles?)[\s._-]?({_TOKEN})(?![A-Za-z])", re.I)
_MULTI_AUDIO = re.compile(r"(?<![A-Za-z])(?:dual|multi|triple)[\s._-]?(?:audio|lang(?:uage)?s?)?(?![A-Za-z])", re.I)
_DUBBED = re.compile(r"(?<![A-Za-z])dubbed?(?![A-Za-z])", re.I)


def lang_code(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    if t in LANG_NAMES:
        return t
    return _ALL.get(t) or _NAME_TO_CODE.get(t)


def lang_name(code: str | None) -> str:
    return LANG_NAMES.get((code or "xx").lower(), "Unknown")


def detect(text: str) -> dict:
    """Return {'audio': [codes], 'subs': [codes], 'multi': bool, 'dubbed': bool}."""
    work = text or ""
    subs: list[str] = []

    def take_subs(m: re.Match) -> str:
        word = m.group(1).lower()
        if word in ("e", "esub"):
            subs.append("en")
        elif word in ("m", "multi"):
            subs.append("multi")
        elif word in _ALL:
            subs.append(_ALL[word])
        else:
            return m.group(0)  # not a language: leave the text alone
        return " "

    work = _SUB_BEFORE.sub(take_subs, work)
    work = _SUB_AFTER.sub(take_subs, work)
    # shorthand: ESub / ESubs / MSub / MSubs
    for m in re.finditer(r"(?<![A-Za-z])([EM])subs?(?![A-Za-z])", work, re.I):
        subs.append("en" if m.group(1).upper() == "E" else "multi")
    work = re.sub(r"(?<![A-Za-z])[EM]subs?(?![A-Za-z])", " ", work, flags=re.I)

    multi = bool(_MULTI_AUDIO.search(work))
    dubbed = bool(_DUBBED.search(work))

    audio: list[str] = []
    run = re.compile(rf"(?<![A-Za-z])({_TOKEN})(?:{_SEP}({_TOKEN}))*(?![A-Za-z])")
    # Walk the text token-by-token, grouping consecutive language tokens.
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z]+", work)]
    i = 0
    while i < len(tokens):
        word, s, e = tokens[i]
        w = word.lower()
        if w in _ALL:
            j = i
            group = [w]
            while j + 1 < len(tokens):
                gap = work[tokens[j][2]: tokens[j + 1][1]]
                nxt = tokens[j + 1][0].lower()
                if nxt in _ALL and re.fullmatch(_SEP, gap or " ") or (nxt in _ALL and re.fullmatch(r"[\s+,/&\-_.\[\]()]*", gap)):
                    group.append(nxt)
                    j += 1
                else:
                    break
            has_full = any(g in FULL_WORDS for g in group)
            if has_full or len(group) >= 2:
                for g in group:
                    code = _ALL[g]
                    if code not in audio:
                        audio.append(code)
            i = j + 1
        else:
            i += 1
    return {"audio": audio, "subs": list(dict.fromkeys(subs)), "multi": multi, "dubbed": dubbed}


def is_language_word(word: str) -> bool:
    return word.lower() in _ALL or bool(_MULTI_AUDIO.fullmatch(word)) or word.lower() in ("dubbed", "dub", "audio")
