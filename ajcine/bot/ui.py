
from __future__ import annotations

import html
import re
from typing import Iterable

from ..brand import FOOTER
from ..catalog import human_size
from ..parsing.languages import lang_name

Row = list[tuple[str, str]]  # (label, callback_data) ; url buttons use ("url:", url)

_CB_OK = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")
ID_RE = r"(?:tt\d{7,10}|loc_[0-9a-f]{10})"
CB_PATTERNS = {
    "t": re.compile(rf"^t:({ID_RE})$"),
    "l": re.compile(rf"^l:({ID_RE}):([a-z]{{2}})$"),
    "q": re.compile(rf"^q:({ID_RE}):([a-z]{{2}}):([A-Za-z0-9]{{2,6}})$"),
    "s": re.compile(rf"^s:({ID_RE})$"),
    "n": re.compile(rf"^n:({ID_RE}):(\d{{1,2}})$"),
    "e": re.compile(rf"^e:({ID_RE}):(\d{{1,2}}):(\d{{1,4}})$"),
    "el": re.compile(rf"^el:({ID_RE}):(\d{{1,2}}):(\d{{1,4}}):([a-z]{{2}})$"),
    "eq": re.compile(rf"^eq:({ID_RE}):(\d{{1,2}}):(\d{{1,4}}):([a-z]{{2}}):([A-Za-z0-9]{{2,6}})$"),
    "sp": re.compile(rf"^sp:({ID_RE}):(\d{{1,2}})$"),
    "cp": re.compile(rf"^cp:({ID_RE})$"),
    "pl": re.compile(rf"^pl:({ID_RE}):(s|c):(\d{{1,2}}):([a-z]{{2}})$"),
    "pq": re.compile(rf"^pq:({ID_RE}):(s|c):(\d{{1,2}}):([a-z]{{2}}):([A-Za-z0-9]{{2,6}})$"),
    "p": re.compile(r"^p:(\d{1,3}):([A-Za-z0-9_\-]{6,24})$"),
    "r": re.compile(r"^r:([A-Za-z0-9_\-]{6,24})$"),
    "w": re.compile(rf"^w:({ID_RE})$"),
    "rq": re.compile(r"^rq:([A-Za-z0-9_\-]{6,24})$"),
}


def parse_cb(data: str | None) -> tuple[str, tuple] | None:
    """Validate and parse callback data; returns (kind, groups) or None."""
    if not data or len(data.encode()) > 64:
        return None
    kind = data.split(":", 1)[0]
    pat = CB_PATTERNS.get(kind)
    if not pat:
        return None
    m = pat.match(data)
    return (kind, m.groups()) if m else None


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def stars(rating: float | None) -> str:
    return f"⭐ {rating:.1f}/10" if rating else ""


def title_card(doc: dict, languages: Iterable[str] | None = None) -> str:
    """Rich text for a title using only real indexed data."""
    kind = "📺 TV Series" if doc.get("kind") == "series" else "🎬 Movie"
    year = f" ({doc['year']})" if doc.get("year") else ""
    lines = [f"<b>{esc(doc.get('title'))}</b>{year}"]
    meta = [kind]
    rating = doc.get("rating", doc.get("imdb_rating"))
    if rating and str(doc.get("_id", doc.get("id", ""))).startswith("tt"):
        meta.append(stars(rating))
    lines.append(" · ".join(meta))
    if doc.get("genres"):
        lines.append("🎭 " + ", ".join(esc(g) for g in doc["genres"][:4]))
    langs = list(languages if languages is not None else doc.get("languages") or [])
    if langs:
        lines.append("🗣 " + ", ".join(lang_name(c) for c in langs))
    if doc.get("overview"):
        o = doc["overview"].strip()
        lines.append("\n" + esc(o if len(o) <= 320 else o[:317].rstrip() + "…"))
    return "\n".join(lines)


def results_text(res: dict) -> str:
    q = esc(res["query"])
    if not res["items"]:
        return f"😕 No results for <b>{q}</b>."
    head = f"🔎 Results for <b>{q}</b>"
    if res.get("interpreted") and res["interpreted"].lower() != res["query"].lower():
        head += f"\n<i>showing matches for “{esc(res['interpreted'])}”</i>"
    head += f"\n{res['total']} found · page {res['page']}/{res['pages']}"
    return head


def results_keyboard(res: dict, token: str) -> list[Row]:
    rows: list[Row] = []
    for it in res["items"]:
        year = f" ({it['year']})" if it.get("year") else ""
        kind = "Series" if it["kind"] == "series" else "Movie"
        rating = f" · {it['rating']:.1f}" if it.get("rating") else ""
        label = f"{it['title']}{year} · {kind}{rating}"
        rows.append([(label[:60], f"t:{it['id']}")])
    nav: Row = []
    if res["page"] > 1:
        nav.append(("Prev", f"p:{res['page'] - 1}:{token}"))
    if res["page"] < res["pages"]:
        nav.append(("Next", f"p:{res['page'] + 1}:{token}"))
    if nav:
        rows.append(nav)
    return rows


def suggestions_keyboard(res: dict) -> list[Row]:
    return [[(f"{s['title']}" + (f" ({s['year']})" if s.get("year") else ""), f"t:{s['id']}")]
            for s in res.get("suggestions", [])]


def language_keyboard(title_id: str, langs: list[dict], pattern: str, back: str | None) -> list[Row]:
    """`pattern` is a callback format with {lang} placeholder."""
    rows: list[Row] = []
    row: Row = []
    for l in langs:
        row.append((f"{l['name']} ({l['files']})", pattern.format(lang=l["code"])))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if back:
        rows.append([("Back", back)])
    return rows


def quality_keyboard(quals: list[dict], pattern: str, back: str | None) -> list[Row]:
    rows: list[Row] = []
    for q in quals:
        label = q["quality"].replace("2160p", "4K 2160p")
        size = f" · {human_size(q['size'])}" if q.get("size") else ""
        count = f" ×{q['files']}" if q["files"] > 1 else ""
        rows.append([(f"{label}{size}{count}", pattern.format(q=q["raw"] or "xx"))])
    if back:
        rows.append([("Back", back)])
    return rows


def seasons_keyboard(title_id: str, seasons: list[dict], complete_pack: bool) -> list[Row]:
    rows: list[Row] = []
    row: Row = []
    for s in seasons:
        row.append((f"Season {s['season']} ({s['episodes']})" if s["episodes"] else f"Season {s['season']} pack",
                    f"n:{title_id}:{s['season']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if complete_pack:
        rows.append([("Complete series pack", f"cp:{title_id}")])
    rows.append([("Back", f"t:{title_id}")])
    return rows


def episodes_keyboard(title_id: str, season: int, eps: list[dict], has_pack: bool, per_row: int = 5) -> list[Row]:
    rows: list[Row] = []
    if has_pack:
        rows.append([(f"Season {season} complete pack", f"sp:{title_id}:{season}")])
    row: Row = []
    for e in eps:
        row.append((f"E{e['episode']:02d}", f"e:{title_id}:{season}:{e['episode']}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("Seasons", f"s:{title_id}")])
    return rows


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF"
                    "\uFE0F\u200D\u20E3\u2139]")


def clean_label(label: str) -> str:
    """Button labels are plain text: emoji and pictographs are removed (safety net for every keyboard)."""
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", label)).strip() or "Open"


def to_markup(rows: list[Row], bot_username: str | None = None):
    """Convert row data into a Pyrogram InlineKeyboardMarkup."""
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    out = []
    for r in rows:
        btns = []
        for label, data in r:
            if data.startswith("url:"):
                btns.append(InlineKeyboardButton(clean_label(label), url=data[4:]))
            else:
                if not _CB_OK.match(data) or len(data.encode()) > 64:
                    continue
                btns.append(InlineKeyboardButton(clean_label(label), callback_data=data))
        if btns:
            out.append(btns)
    return InlineKeyboardMarkup(out)


def credit_footer() -> str:
    return f"\n\n<i>{FOOTER}</i>"
