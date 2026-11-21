"""Deterministic release-filename parser (first stage of identification).

Pure string work, no network. The AI layer is only consulted when this stage
plus TMDB matching cannot produce a confident, verified identification.

Design reference: the "boundary first, lists last" approach used by the MIT
licensed muse-tg-media-search-bot parser (Saurav Rebhankar). This is an
independent implementation.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

from .languages import detect, is_language_word, lang_name
from .text import match_key

VIDEO_EXTS = {"mkv", "mp4", "avi", "mov", "webm", "m4v", "ts", "wmv", "flv", "mpg", "mpeg", "m2ts", "3gp", "mts"}
AUDIO_EXTS = {"mp3", "flac", "aac", "m4a", "ogg", "opus", "wav", "ac3", "eac3", "dts", "mka"}
ARCHIVE_EXTS = {"zip", "rar", "7z"}
KNOWN_EXTS = VIDEO_EXTS | AUDIO_EXTS | ARCHIVE_EXTS | {"srt", "ass", "sub", "idx", "txt", "nfo", "jpg", "png", "pdf", "apk", "exe", "iso"}

RES_RE = re.compile(r"(?<![A-Za-z0-9])(2160p|1440p|1080p|1080i|720p|576p|540p|480p|360p|240p|4k|uhd|8k)(?![A-Za-z0-9])", re.I)
SOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(WEB[\s.\-]?DL|WEB[\s.\-]?Rip|WEB|Blu[\s.\-]?Ray|BDRemux|BD[\s.\-]?Rip|BR[\s.\-]?Rip|Remux|"
    r"HD[\s.\-]?Rip|HDTV|PDTV|DVD[\s.\-]?Rip|DVD[\s.\-]?Scr|DVD|HD[\s.\-]?CAM|CAM[\s.\-]?Rip|CAM|HDTS|"
    r"TeleSync|TeleCine|Pre[\s.\-]?DVD|HDTC|SCR|TS|TC)(?![A-Za-z0-9])", re.I)
SERVICE_RE = re.compile(
    r"(?<![A-Za-z0-9])(AMZN|NF|DSNP|ZEE5|SONYLIV|SUNNXT|HULU|HBO|ATVP|PCOK|HOTSTAR|AHA|JIOCINEMA|MX)(?![A-Za-z0-9])")
VCODEC_RE = re.compile(
    r"(?<![A-Za-z0-9])(HEVC|x[\s.]?265|H[\s.]?265|x[\s.]?264|H[\s.]?264|AVC|AV1|XviD|DivX|VP9)(?![A-Za-z0-9])", re.I)
BITDEPTH_RE = re.compile(r"(?<![A-Za-z0-9])(10[\s\-]?bit|8[\s\-]?bit)(?![A-Za-z0-9])", re.I)
AUDIO_RE = re.compile(
    r"(?<![A-Za-z0-9])(DDP|DD\+|DD|E[\s\-]?AC[\s\-]?3|EAC3|AC3|AAC|DTS[\s\-]?HD(?:[\s.\-]?MA)?|DTS[\s\-]?X|DTS|TrueHD|FLAC|OPUS|MP3|LPCM)"
    r"(?:[\s.\-]?(1[\s.]0|2[\s.]0|5[\s.]1|7[\s.]1))?(?![A-Za-z0-9])", re.I)
ATMOS_RE = re.compile(r"(?<![A-Za-z0-9])Atmos(?![A-Za-z0-9])", re.I)
HDR_RE = re.compile(
    r"(?<![A-Za-z0-9])(HDR10\+|HDR10|HDR|DoVi|DV|Dolby[\s.]?Vision|HLG|SDR)(?![A-Za-z0-9+])", re.I)
EDITION_RE = re.compile(
    r"(?<![A-Za-z0-9])(Extended|Director'?s[\s.]?Cut|Unrated|Remastered|IMAX|Theatrical|Uncut|Special[\s.]?Edition|"
    r"Final[\s.]?Cut|Open[\s.]?Matte|Ultimate[\s.]?Edition|Criterion)(?![A-Za-z0-9])", re.I)
CAM_SOURCES = {"CAM", "HDCAM", "CAMRIP", "HDTS", "TS", "TELESYNC", "TC", "HDTC", "TELECINE", "PREDVD", "SCR", "DVDSCR"}

YEAR_RE = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
YEAR_PAREN_RE = re.compile(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]")

SE_RE = re.compile(
    r"(?<![A-Za-z0-9])S(\d{1,2})[\s._\-]?E(\d{1,3})(?:[\s._\-]?(?:E|\-E?|to)[\s._\-]?(\d{1,3}))?(?![0-9])", re.I)
NXN_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})x(\d{2,3})(?![0-9A-Za-z])", re.I)
SEASON_EP_WORDS_RE = re.compile(
    r"Season[\s._\-]?(\d{1,2})[\s._\-,]*(?:Episode|Ep)[\s._\-]?(\d{1,3})", re.I)
SEASON_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:S|Seasons?[\s._\-]?)(\d{1,2})[\s._\-]*(?:\-|to|~)[\s._\-]*(?:S|Seasons?[\s._\-]?)?(\d{1,2})(?![0-9A-Za-z])", re.I)
SEASON_ONLY_RE = re.compile(r"(?<![A-Za-z0-9])(?:Season[\s._\-]?|S)(\d{1,2})(?![0-9A-Za-z])", re.I)
EP_ONLY_RE = re.compile(r"(?<![A-Za-z0-9])(?:EP|Episode|E)[\s._\-]?(\d{1,3})(?![0-9A-Za-z])", re.I)
PACK_RE = re.compile(
    r"(?<![A-Za-z0-9])(complete[\s._\-]?(?:series|collection|seasons?|season)?|all[\s._\-]seasons|full[\s._\-]series|"
    r"combined|collection)(?![A-Za-z0-9])", re.I)
COMPLETE_SERIES_RE = re.compile(
    r"(?<![A-Za-z0-9])(complete[\s._\-]?(?:series|collection)|all[\s._\-]seasons|full[\s._\-]series)(?![A-Za-z0-9])", re.I)

SOFT_RE = re.compile(
    r"(?<![A-Za-z0-9])(full[\s._\-]?movie|watch[\s._\-]?online|download|proper|repack|internal|"
    r"dubbed?|dual[\s._\-]?audio|multi[\s._\-]?audio|esubs?|msubs?)(?![A-Za-z0-9])", re.I)

JUNK_PREFIX_RE = re.compile(
    r"^\s*(?:(?:@[\w]+|www\.[\w.\-]+\.\w{2,5}|https?://\S+)\s*[\-–—_.:|]*\s*)+")
LEADING_BRACKET_RE = re.compile(r"^\s*[\[\(\{]([^\]\)\}]{1,40})[\]\)\}]\s*[\-–—_.:|]*\s*")
TRAILING_GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,15})(?:\[[^\]]*\])?$")
GROUP_BLACKLIST = {"dl", "rip", "ray", "dub", "sub", "subs", "web", "hd", "hdr", "dts", "ma", "x", "dd", "ddp", "aac", "mkv", "eng", "hin", "mal", "tam", "tel"}


@dataclass
class ParsedName:
    raw: str = ""
    title: str = ""
    year: int | None = None
    content_type: str = "unknown"  # movie | series | unknown
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    season_end: int | None = None
    is_season_pack: bool = False
    is_complete_series: bool = False
    season_assumed: bool = False
    resolution: str | None = None      # 2160p/1080p/720p/480p/...
    quality: str | None = None         # normalized bucket used for navigation
    source: str | None = None
    service: str | None = None
    video_codec: str | None = None
    bit_depth: str | None = None
    audio_codec: str | None = None
    audio_extras: list[str] = field(default_factory=list)
    hdr: list[str] = field(default_factory=list)
    edition: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)       # codes
    subtitles: list[str] = field(default_factory=list)
    multi_audio: bool = False
    dubbed: bool = False
    release_group: str | None = None
    is_cam: bool = False
    extension: str | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def language_names(self) -> list[str]:
        return [lang_name(c) for c in self.languages]

    @property
    def title_key(self) -> str:
        t = match_key(self.title)
        if self.content_type == "series":
            return f"series:{t}"
        if self.content_type == "movie":
            return f"movie:{t}:{self.year or ''}"
        return f"unk:{t}:{self.year or ''}"


def _norm_res(token: str) -> str:
    t = token.lower()
    if t in ("4k", "uhd"):
        return "2160p"
    if t == "8k":
        return "4320p"
    if t.endswith("i"):
        return t[:-1] + "p"
    return t


def _norm_vcodec(token: str) -> str:
    t = re.sub(r"[\s.]", "", token).lower()
    if t in ("hevc", "x265", "h265"):
        return "HEVC"
    if t in ("x264", "h264", "avc"):
        return "x264"
    if t == "av1":
        return "AV1"
    return token.upper() if t == "vp9" else token


def _norm_audio(codec: str, chan: str | None) -> str:
    c = re.sub(r"[\s\-]", "", codec).upper()
    c = {"DD+": "DDP", "EAC3": "DDP", "EAC": "DDP"}.get(c, c)
    if c.startswith("DTSHD"):
        c = "DTS-HD MA" if "MA" in c else "DTS-HD"
    if c == "DTSX":
        c = "DTS-X"
    if c == "TRUEHD":
        c = "TrueHD"
    ch = re.sub(r"[\s]", ".", chan) if chan else ""
    return f"{c}{ch}" if c not in ("DTS-HD MA",) else f"{c} {ch}".strip()


def _flatten(s: str) -> str:
    """Replace separators with spaces but keep dots between digits (5.1, 7.1)."""
    s = s.replace("_", " ")
    return re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)


def _clean_title(t: str) -> str:
    t = re.sub(r"[\[\(\{]\s*$", "", t.strip())
    t = re.sub(r"\s+", " ", t).strip(" -–—_.,:;|[]()")
    return t


def _pretty(t: str) -> str:
    if t and (t.isupper() or t.islower()):
        small = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for", "vs"}
        words = t.split(" ")
        out = []
        for i, w in enumerate(words):
            lw = w.lower()
            out.append(lw if (i and lw in small) else lw.capitalize())
        return " ".join(out)
    return t


def parse_filename(name: str | None) -> ParsedName:
    p = ParsedName(raw=name or "")
    if not name or not name.strip():
        return p
    base = os.path.basename(name.strip())
    m = re.search(r"\.([A-Za-z0-9]{2,5})$", base)
    if m and m.group(1).lower() in KNOWN_EXTS:
        p.extension = m.group(1).lower()
        base = base[: m.start()]
    # junk prefixes: @channel, www.site.com, [tags]
    base = JUNK_PREFIX_RE.sub("", base)
    lead = LEADING_BRACKET_RE.match(base)
    lead_group = None
    if lead and not re.search(r"\d{3,4}p|(19|20)\d{2}|S\d+E\d+", lead.group(1), re.I):
        lead_group = lead.group(1).strip()
        base = base[lead.end():]

    s = base.replace("_", " ")
    flat = _flatten(base)

    # ---- release group -------------------------------------------------
    gm = TRAILING_GROUP_RE.search(s.strip())
    if gm and gm.group(1).lower() not in GROUP_BLACKLIST and not RES_RE.fullmatch(gm.group(1)):
        before = s[: gm.start()]
        if RES_RE.search(before) or SOURCE_RE.search(before) or VCODEC_RE.search(before) or AUDIO_RE.search(before):
            p.release_group = gm.group(1)
    if not p.release_group and lead_group:
        p.release_group = lead_group

    # ---- hard technical tokens (positions on `flat`) ------------------
    spans: list[int] = []

    rm = RES_RE.search(flat)
    if rm:
        p.resolution = _norm_res(rm.group(1))
        spans.append(rm.start())

    for sm in SOURCE_RE.finditer(flat):
        tok = re.sub(r"[\s.\-]", "", sm.group(1)).upper()
        # "TS"/"TC"/"SCR" are ambiguous: only trust them after a title-like start
        if tok in ("TS", "TC", "SCR") and sm.start() == 0:
            continue
        if p.source is None:
            canon = {"WEBDL": "WEB-DL", "WEBRIP": "WEBRip", "WEB": "WEB", "BLURAY": "BluRay", "BDRIP": "BDRip",
                     "BRRIP": "BRRip", "BDREMUX": "Remux", "REMUX": "Remux", "HDRIP": "HDRip", "DVDRIP": "DVDRip",
                     "DVDSCR": "DVDScr", "HDCAM": "HDCAM", "CAMRIP": "CAMRip", "TELESYNC": "TeleSync",
                     "TELECINE": "TeleCine", "PREDVD": "PreDVD"}.get(tok, tok)
            p.source = canon
        if tok in CAM_SOURCES:
            p.is_cam = True
        spans.append(sm.start())

    sv = SERVICE_RE.search(flat)
    if sv and sv.start() > 0:
        p.service = sv.group(1)
        spans.append(sv.start())

    vm = VCODEC_RE.search(flat)
    if vm:
        p.video_codec = _norm_vcodec(vm.group(1))
        spans.append(vm.start())
    bm = BITDEPTH_RE.search(flat)
    if bm:
        p.bit_depth = re.sub(r"[\s\-]", "", bm.group(1)).lower()
        spans.append(bm.start())

    # audio is read from `s` where "5.1" keeps its dot
    am_all = list(AUDIO_RE.finditer(s))
    if am_all:
        am = am_all[0]
        p.audio_codec = _norm_audio(am.group(1), am.group(2))
        for extra in am_all[1:]:
            e = _norm_audio(extra.group(1), extra.group(2))
            if e != p.audio_codec and e not in p.audio_extras:
                p.audio_extras.append(e)
        am_flat = AUDIO_RE.search(flat)
        if am_flat:
            spans.append(am_flat.start())
    if ATMOS_RE.search(s):
        p.audio_extras.append("Atmos")

    seen_hdr: list[str] = []
    for hm in HDR_RE.finditer(flat):
        v = hm.group(1).upper().replace(" ", "").replace(".", "")
        v = "DV" if v in ("DOVI", "DOLBYVISION") else v
        if v not in seen_hdr:
            seen_hdr.append(v)
        spans.append(hm.start())
    p.hdr = [h for h in seen_hdr if h != "SDR"]

    for em in EDITION_RE.finditer(flat):
        e = re.sub(r"[\s.]+", " ", em.group(1)).title().replace("Director'S", "Director's")
        e = "IMAX" if e.upper() == "IMAX" else e
        if e not in p.edition:
            p.edition.append(e)
        spans.append(em.start())

    # ---- season / episode ------------------------------------------------
    se_start = None
    m1 = SE_RE.search(flat)
    m2 = SEASON_EP_WORDS_RE.search(flat)
    m3 = NXN_RE.search(flat)
    if m1:
        p.season, p.episode = int(m1.group(1)), int(m1.group(2))
        if m1.group(3):
            end = int(m1.group(3))
            if p.episode < end <= p.episode + 60:
                p.episode_end = end
        se_start = m1.start()
    elif m2:
        p.season, p.episode = int(m2.group(1)), int(m2.group(2))
        se_start = m2.start()
    elif m3 and int(m3.group(1)) <= 40 and not RES_RE.search(m3.group(0)):
        p.season, p.episode = int(m3.group(1)), int(m3.group(2))
        se_start = m3.start()
    if p.episode is None:
        rr = SEASON_RANGE_RE.search(flat)
        if rr and int(rr.group(1)) < int(rr.group(2)) <= 60:
            p.season, p.season_end = int(rr.group(1)), int(rr.group(2))
            p.is_complete_series = True
            se_start = rr.start()
        else:
            so = SEASON_ONLY_RE.search(flat)
            if so:
                p.season = int(so.group(1))
                p.is_season_pack = True
                se_start = so.start()
    if p.season is None and p.episode is None:
        eo = EP_ONLY_RE.search(flat)
        # bare E05 / EP05 / Episode 5: anime-style numbering
        if eo and eo.start() > 0:
            p.episode, p.season, p.season_assumed = int(eo.group(1)), 1, True
            se_start = eo.start()
    cs = COMPLETE_SERIES_RE.search(flat)
    if cs:
        p.is_complete_series = True
        if p.season is None:
            p.is_season_pack = False
        spans.append(cs.start())
    elif p.season is not None and p.episode is None:
        pk = PACK_RE.search(flat)
        if pk:
            p.is_season_pack = True
            spans.append(pk.start())
    if se_start is not None:
        spans.append(se_start)

    # ---- year -----------------------------------------------------------
    year_pos = None
    years = [(m.start(), int(m.group(1))) for m in YEAR_RE.finditer(flat)]
    paren = YEAR_PAREN_RE.search(base)
    if paren:
        y = int(paren.group(1))
        p.year = y
        pm = [pos for pos, v in years if v == y]
        year_pos = pm[-1] if pm else None
    else:
        cands = [(pos, v) for pos, v in years if pos > 0]
        # prefer the last year that sits before the first technical token
        before = [(pos, v) for pos, v in cands if pos <= (min(spans) if spans else len(flat))]
        chosen = (before or cands or [None])[-1]
        if chosen:
            year_pos, p.year = chosen
    if year_pos is not None:
        spans.append(year_pos)

    # ---- languages ----------------------------------------------------------
    lang = detect(s if "Sub" in s or "sub" in s else flat)
    p.languages = lang["audio"]
    p.subtitles = lang["subs"]
    p.multi_audio = lang["multi"] or len(p.languages) > 1
    p.dubbed = lang["dubbed"]

    # ---- title ------------------------------------------------------------
    soft = SOFT_RE.search(flat)
    boundary = min(spans) if spans else None
    if soft and (boundary is None or soft.start() < boundary) and soft.start() > 0:
        boundary = soft.start()
    title_raw = flat[:boundary] if boundary is not None else flat
    # a language run directly after the title also ends it ("Premalu Malayalam")
    words = title_raw.split()
    while len(words) > 1 and (is_language_word(words[-1]) or words[-1].lower() in ("hq", "hd", "full", "movie", "bluray")):
        words.pop()
    title = _clean_title(" ".join(words))
    # strip bracketed/parenthesised language or year debris left in the title
    title = re.sub(r"[\[\(][^\]\)]*$", "", title).strip()
    p.title = _pretty(title)

    # ---- classification ---------------------------------------------------
    if p.season is not None or p.episode is not None or p.is_complete_series:
        p.content_type = "series"
    elif p.title:
        p.content_type = "movie" if (p.year or p.resolution or p.source or p.extension in VIDEO_EXTS) else "unknown"

    # quality bucket for navigation
    if p.resolution:
        p.quality = p.resolution
    elif p.source in ("HDRip", "DVDRip", "DVDScr", "DVD", "CAM", "HDCAM", "CAMRip", "TS", "HDTS", "TeleSync"):
        p.quality = "SD"

    conf = 0.2
    conf += 0.3 if len(p.title) >= 2 else 0
    conf += 0.15 if p.year else 0
    conf += 0.1 if p.resolution else 0
    conf += 0.1 if (p.season is not None or p.source) else 0
    conf += 0.1 if p.languages else 0
    p.confidence = round(min(conf, 1.0), 2)
    return p


def merge_parsed(primary: ParsedName, secondary: ParsedName) -> ParsedName:
    """Fill gaps in `primary` (file name) from `secondary` (caption)."""
    out = ParsedName(**{k: v for k, v in primary.to_dict().items()})
    sec = secondary.to_dict()
    for k, v in sec.items():
        cur = getattr(out, k)
        if k in ("raw", "confidence"):
            continue
        if cur in (None, "", [], False, "unknown") and v not in (None, "", [], False, "unknown"):
            setattr(out, k, v)
    return out
