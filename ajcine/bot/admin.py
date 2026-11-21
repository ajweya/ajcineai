"""Button-driven Telegram administrator panel.

Entry: the "Admin panel" button on the start menu (shown to admins only).
Every callback re-checks the caller's role; callback data is validated.
Free-text input (adding a source, searching titles...) uses a short-lived
conversational state kept in the cache.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from .. import brand
from .. import db as dbm
from ..app import App
from ..logging_setup import recent_logs
from ..users import role_allows
from .runner import edit_view, safe, to_markup  # noqa: F401  (runner is fully loaded before admin is imported)

log = logging.getLogger("ajcine.bot.admin")

CB_RE = re.compile(r"^a:[a-z]{2,6}(?::[A-Za-z0-9_.\-]{1,40}){0,3}(?::-?\d{1,20}:\d{1,12})?$")
FID_RE = re.compile(r"^-?\d{1,20}:\d{1,12}$")
STATE_TTL = 300

SECTION_PERM = {
    "home": "view", "dash": "view", "st": "view", "lg": "view", "sy": "view",
    "rv": "review", "idx": "indexing", "src": "sources", "ai": "ai", "im": "tmdb",
    "us": "users", "se": "settings", "ps": "media_edit", "web": "settings", "bc": "broadcast", "ad": "ads",
}


def esc(s: Any) -> str:
    import html
    return html.escape(str(s if s is not None else ""), quote=False)


def menu_rows() -> list:
    return [
        [("Dashboard", "a:dash"), ("Statistics", "a:st")],
        [("Indexing", "a:idx"), ("Source channels", "a:src")],
        [("Media review", "a:rv"), ("AI identification", "a:ai")],
        [("TMDB", "a:im"), ("Users", "a:us")],
        [("Poster", "a:ps"), ("Settings", "a:se")],
        [("Logs", "a:lg"), ("System status", "a:sy")],
        [("Website & streaming", "a:web")],
        [("Broadcast", "a:bc"), ("Ads", "a:ad")],
        [("Back to bot", "h:menu")],
    ]


BACK = [[("Admin menu", "a:home")]]


async def _state_set(app: App, uid: int, **data) -> None:
    await app.cache.set(f"adm_state:{uid}", data, ttl=STATE_TTL)


async def _state_get(app: App, uid: int) -> dict | None:
    return await app.cache.get(f"adm_state:{uid}")


async def _state_clear(app: App, uid: int) -> None:
    await app.cache.delete(f"adm_state:{uid}")


def _fmt_bytes(n: int | None) -> str:
    from ..catalog import human_size
    return human_size(n)


def _uptime(app: App) -> str:
    s = int(time.time() - app.started_at)
    return f"{s // 86400}d {s % 86400 // 3600}h {s % 3600 // 60}m"


# ================================================================= callbacks
def make_callback(app: App):
    @safe
    async def on_admin(client, cb):
        uid = cb.from_user.id
        role = await app.users.telegram_role(uid)
        if role is None:
            return await cb.answer("Not authorized.", show_alert=True)
        data = cb.data or ""
        if not CB_RE.match(data) and not data.startswith("a:rv"):
            return await cb.answer("Invalid action.", show_alert=False)
        parts = data.split(":")
        section = parts[1]
        need = SECTION_PERM.get(section) or SECTION_PERM.get(section[:2])
        if need is None:
            sec2 = {"srcv": "sources", "srca": "sources", "srct": "sources", "srcr": "sources", "srcl": "sources",
                    "srce": "sources", "srcp": "sources",
                    "jb": "indexing", "jbp": "indexing", "jbr": "indexing", "jbc": "indexing", "jbs": "indexing",
                    "rva": "review", "rvc": "review", "rvq": "review", "rvx": "review", "rvs": "review",
                    "rvl": "review", "rvn": "review",
                    "aim": "ai", "aif": "ai", "aie": "ai", "aid": "ai", "ait": "ai", "ail": "ai",
                    "imc": "tmdb", "imh": "tmdb",
                    "usb": "users", "usu": "users", "usi": "users", "usl": "users",
                    "sec": "cache"}.get(section)
            need = sec2
        if need is None or not role_allows(role, need):
            return await cb.answer("You do not have permission for this.", show_alert=True)
        actor = {"type": "telegram", "id": uid}
        try:
            await route(app, client, cb, parts, actor)
        finally:
            try:
                await cb.answer()
            except Exception:
                pass
    return on_admin


async def route(app: App, client, cb, p: list[str], actor: dict) -> None:
    sec = p[1]
    uid = cb.from_user.id

    if sec == "home":
        await _state_clear(app, uid)
        role = await app.users.telegram_role(uid)
        return await edit_view(cb, f"⚙️ <b>{brand.PRODUCT} · Admin</b>\nRole: <b>{role}</b>\n\n<i>{brand.FOOTER}</i>", menu_rows())

    if sec == "dash":
        st = await app.catalog.stats()
        text = (f"📊 <b>Dashboard</b>\n\n"
                f"📁 Files: <b>{st['files']}</b> (identified {st['files_identified']})\n"
                f"🎬 Movies: <b>{st['movies']}</b> · 📺 Series: <b>{st['series']}</b>\n"
                f"Seasons: {st['seasons']} · Episodes: {st['episodes']}\n"
                f"👥 Users: <b>{st['users']}</b> · 🔎 Searches: {st['searches']} · 📥 Deliveries: {st['deliveries']}\n\n"
                f"🧐 Review queue: <b>{st['review_queue']}</b> · ❓ Unidentified: {st['files_unidentified']}\n"
                f"🗂 Jobs running: {st['jobs_running']} · ❌ Failed: {st['jobs_failed']}\n"
                f"⚠️ Missing sources: {st['files_missing']}")
        return await edit_view(cb, text, [[("Refresh", "a:dash")], *BACK])

    if sec == "st":
        pop = [d async for d in app.db[dbm.SEARCH_HISTORY].aggregate(
            [{"$group": {"_id": "$norm_query", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 8}])]
        zero = await app.db[dbm.SEARCH_HISTORY].count_documents({"results": 0})
        lines = "\n".join(f"{i + 1}. {esc(d['_id'])} — {d['n']}" for i, d in enumerate(pop)) or "No searches yet."
        usage = await app.openrouter.usage(1)
        today = next((u for u in usage if u.get("model") == "_all"), {})
        text = (f"📈 <b>Statistics</b>\n\n<b>Top searches</b>\n{lines}\n\nSearches with no results: {zero}\n\n"
                f"<b>AI today</b>: {today.get('requests', 0)} requests · {today.get('success', 0)} ok · "
                f"{today.get('errors', 0)} errors · {today.get('cache_hits', 0)} cache hits")
        return await edit_view(cb, text, [[("Refresh", "a:st")], *BACK])

    if sec == "lg":
        logs = recent_logs(15)
        body = "\n".join(f"{l['ts'][11:]} {l['level'][:4]} {esc(l['message'][:110])}" for l in logs) or "No logs yet."
        return await edit_view(cb, f"📜 <b>Recent logs</b>\n<pre>{body}</pre>"[:3900],
                               [[("Refresh", "a:lg"), ("Errors only", "a:lg:err")], *BACK]) if len(p) < 3 else \
            await edit_view(cb, "📜 <b>Errors</b>\n<pre>" + ("\n".join(f"{l['ts'][11:]} {esc(l['message'][:120])}" for l in recent_logs(15, "ERROR")) or "No errors.") + "</pre>",
                            [[("Refresh", "a:lg:err")], *BACK])

    if sec == "sy":
        mongo_ok = True
        try:
            health = await app.db.ping() if hasattr(app.db, "ping") else {}
            mongo_ok = all(health.values()) if health else True
            bad_roles = [r for r, ok in health.items() if not ok]
        except Exception:
            mongo_ok, bad_roles = False, []
        tmdb_ok = app.lookup.enabled
        ai_s = await app.openrouter.status()
        text = (f"🩺 <b>System status</b>\n\n"
                f"Uptime: {_uptime(app)}\nMongoDB: {'✅' if mongo_ok else '❌ ' + ', '.join(bad_roles)}\n"
                f"Cache: {app.cache.backend} {'✅' if await app.cache.ping() else '❌'}\n"
                f"TMDB: {'✅ configured' if tmdb_ok else '❌ not configured'}\n"
                f"AI: {'✅ on' if ai_s['effective_enabled'] else '⏸ off'}\n"
                f"Bot client: {'✅' if app.tg else '❌'} · User session: {'✅' if app.tg_user else '—'}\n\n<i>{brand.FOOTER}</i>")
        return await edit_view(cb, text, [[("Refresh", "a:sy")], *BACK])

    # ------------------------------------------------------------------ sources
    if sec == "src":
        srcs = [d async for d in app.db[dbm.SOURCES].find({}).sort("created_at", 1)]
        rows = [[(f"{'' if s.get('enabled') else ''} {s.get('title', s['_id'])[:34]}", f"a:srcv:{s['_id']}")] for s in srcs]
        rows.append([("Add source", "a:srca")])
        rows += BACK
        return await edit_view(cb, f"📡 <b>Source channels</b>\n{len(srcs)} configured.\nThe bot (or the user session) must be a member/admin.", rows)

    if sec == "srca":
        await _state_set(app, uid, a="srcadd")
        return await edit_view(cb, "➕ <b>Add source</b>\nSend the channel/group <b>@username</b>, a <b>t.me link</b>, the numeric <b>ID</b> "
                                   "(-100…), or <b>forward any message</b> from it.", [[("Cancel", "a:src")]])

    if sec == "srcv":
        cid = int(p[2])
        s = await app.db[dbm.SOURCES].find_one({"_id": cid})
        if not s:
            return await edit_view(cb, "Source not found.", [[("Sources", "a:src")]])
        st = s.get("stats", {})
        files = await app.db[dbm.FILES].count_documents({"source_chat_id": cid, "deleted": {"$ne": True}})
        text = (f"📡 <b>{esc(s.get('title'))}</b>\nID <code>{cid}</code>\nEnabled: {s.get('enabled')} · Live: {s.get('live', True)}\n"
                f"Files: {files} · indexed {st.get('indexed', 0)} · dup {st.get('duplicate', 0)} · failed {st.get('failed', 0)}\n"
                f"Last message id: {s.get('last_message_id', 0)}")
        rows = [[("Index history", f"a:srcp:{cid}"), ("Re-index", f"a:srcr:{cid}")],
                [("Toggle enabled", f"a:srce:{cid}"), ("Toggle live", f"a:srcl:{cid}")],
                [("Test access", f"a:srct:{cid}"), ("Remove", f"a:srcx:{cid}")],
                [("Sources", "a:src")]]
        return await edit_view(cb, text, rows)

    if sec in ("srce", "srcl"):
        cid = int(p[2])
        s = await app.db[dbm.SOURCES].find_one({"_id": cid}) or {}
        if sec == "srce":
            await app.indexer.set_source(cid, enabled=not s.get("enabled", True))
        else:
            await app.indexer.set_source(cid, live=not s.get("live", True))
        return await route(app, client, cb, ["a", "srcv", str(cid)], actor)

    if sec == "srct":
        ok, why = await app.indexer.access_test(int(p[2]))
        return await cb.answer(("✅ Access OK" if ok else f"❌ {why}")[:190], show_alert=True)

    if sec in ("srcp", "srcr"):
        cid = int(p[2])
        try:
            jid = await app.indexer.start_job(cid, "history" if sec == "srcp" else "reindex")
        except ValueError as exc:
            return await cb.answer(str(exc), show_alert=True)
        await cb.answer("Job started", show_alert=False)
        return await route(app, client, cb, ["a", "idx"], actor)

    if sec == "srcx":
        if len(p) > 3 and p[3] == "y":
            await app.indexer.remove_source(int(p[2]), purge=False)
            return await route(app, client, cb, ["a", "src"], actor)
        return await edit_view(cb, "🗑 Remove this source? Indexed files stay in the catalog.",
                               [[("Yes, remove", f"a:srcx:{p[2]}:y"), ("Cancel", f"a:srcv:{p[2]}")]])

    # ----------------------------------------------------------------- indexing jobs
    if sec == "idx":
        jobs = await app.indexer.list_jobs(8)
        rows, lines = [], []
        for j in jobs:
            pct = ""
            if j.get("end_id") and j.get("cursor"):
                pct = f" {min(100, int(j['cursor'] * 100 / max(1, j['end_id'])))}%"
            lines.append(f"• <code>{j['_id']}</code> {j['kind']} <b>{j['status']}</b>{pct} · idx {j.get('indexed', 0)} · fail {j.get('failed', 0)}")
            if j["status"] in ("running", "queued"):
                rows.append([(f"{j['_id'][:6]}", f"a:jbp:{j['_id']}"), (f"{j['_id'][:6]}", f"a:jbc:{j['_id']}")])
            elif j["status"] in ("paused", "failed"):
                rows.append([(f"{j['_id'][:6]}", f"a:jbr:{j['_id']}"), (f"{j['_id'][:6]}", f"a:jbc:{j['_id']}")])
        rows += [[("Refresh", "a:idx"), ("Sources", "a:src")], *BACK]
        err = [j for j in jobs if j.get("error")]
        extra = f"\n\n⚠️ {esc(err[0]['error'])}" if err else ""
        return await edit_view(cb, "🗂 <b>Indexing jobs</b>\n" + ("\n".join(lines) or "No jobs yet.") + extra, rows)

    if sec in ("jbp", "jbr", "jbc"):
        jid = p[2]
        fn = {"jbp": app.indexer.pause_job, "jbr": app.indexer.resume_job, "jbc": app.indexer.cancel_job}[sec]
        await fn(jid)
        return await route(app, client, cb, ["a", "idx"], actor)

    # -------------------------------------------------------------------- review
    if sec in ("rv", "rvn"):
        skip = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        q = await app.review.queue("pending", page=skip + 1, per_page=1)
        if not q["items"]:
            return await edit_view(cb, "✅ Review queue is empty.", BACK)
        it = q["items"][0]
        fid = it["file_id"]
        full = await app.review.item(fid)
        cands = full["candidates"][:5]
        lines = [f"🧐 <b>Review</b> ({skip + 1}/{q['total']})", f"<code>{esc(it['file_name'])}</code>", ""]
        pr = it.get("parsed") or {}
        lines.append(f"Parsed: <b>{esc(pr.get('title'))}</b> {pr.get('year') or ''} · {esc(pr.get('content_type'))}"
                     + (f" · S{pr['season']:02d}E{pr.get('episode') or 0:02d}" if pr.get("season") is not None else ""))
        lines.append(f"Reason: {esc(it.get('reason'))}")
        if full.get("ai"):
            lines.append(f"AI: {esc(full['ai'].get('title'))} {full['ai'].get('year') or ''} (conf {full['ai'].get('confidence', 0):.2f})")
        rows = []
        for i, c in enumerate(cands):
            lines.append(f"{i + 1}. {esc(c['title'])} ({c.get('year') or '?'}) {c['kind']} · {c.get('score', 0):.2f}")
            rows.append([(f"{i + 1}. {c['title'][:28]} ({c.get('year') or '?'})", f"a:rvc:{i}:{fid}")])
        rows += [[("Search titles", f"a:rvs:{fid}"), ("Local title", f"a:rvl:{fid}")],
                 [("Reject", f"a:rvx:{fid}"), ("Skip", f"a:rvn:{skip + 1}")], *BACK]
        return await edit_view(cb, "\n".join(lines)[:3900], rows)

    if sec in ("rvc", "rvq"):
        idx, fid = int(p[2]), ":".join(p[3:5])
        if not FID_RE.match(fid):
            return await cb.answer("Bad id", show_alert=True)
        if sec == "rvc":
            full = await app.review.item(fid)
            cands = full["candidates"]
        else:
            cands = await app.cache.get(f"rvs:{cb.from_user.id}") or []
        if idx >= len(cands):
            return await cb.answer("Expired. Open the item again.", show_alert=True)
        res = await app.review.approve(fid, cands[idx]["imdb_id"], actor, scope="group")
        await cb.answer(f"Approved {res['approved']} file(s)", show_alert=False)
        return await route(app, client, cb, ["a", "rv"], actor)

    if sec == "rvx":
        fid = ":".join(p[2:4])
        if not FID_RE.match(fid):
            return await cb.answer("Bad id", show_alert=True)
        await app.review.reject(fid, actor, scope="file")
        return await route(app, client, cb, ["a", "rv"], actor)

    if sec in ("rvs", "rvl"):
        fid = ":".join(p[2:4])
        if not FID_RE.match(fid):
            return await cb.answer("Bad id", show_alert=True)
        if sec == "rvs":
            await _state_set(app, uid, a="rvsearch", fid=fid)
            msg = "🔍 Send the title to search for (optionally add the year, e.g. <i>Premalu 2024</i>)."
        else:
            await _state_set(app, uid, a="rvlocal", fid=fid)
            msg = "📝 Send <b>Title | year | movie</b> (or <b>series</b>). Use this only for titles TMDB does not have."
        return await edit_view(cb, msg, [[("Cancel", "a:rv")]])

    # ------------------------------------------------------------------------- AI
    if sec == "ai":
        s = await app.openrouter.status()
        ms = "\n".join(f"{'🟢' if m['verified_free'] else '🔴 NOT FREE'} {esc(m['id'])} ({m['role']})" for m in s["models"]) or "No models set."
        t = s["today"]
        text = (f"🤖 <b>AI identification</b>\nKey: {'✅' if s['key_configured'] else '❌ missing'} · Enabled: <b>{s['enabled']}</b>\n"
                f"Catalogue reachable: {s['catalogue_reachable']}\n\n<b>Models (free only)</b>\n{ms}\n\n"
                f"Today: {t.get('requests', 0)} req · {t.get('success', 0)} ok · {t.get('errors', 0)} err · {t.get('rate_limited', 0)} 429"
                + ("\n⚠️ Daily free limit reached" if s["daily_limit_reached"] else ""))
        rows = [[("Enable/Disable", "a:aie"), ("Test", "a:ait")],
                [("Set model", "a:aim"), ("Set fallbacks", "a:aif")],
                [("Free models", "a:ail"), ("Refresh", "a:ai")], *BACK]
        return await edit_view(cb, text, rows)

    if sec == "aie":
        cfg = await app.openrouter.config()
        await app.openrouter.set_config(enabled=not cfg["enabled"])
        return await route(app, client, cb, ["a", "ai"], actor)

    if sec == "ait":
        r = await app.openrouter.test()
        return await cb.answer(("✅ OK via " + r["model"]) if r["ok"] else ("❌ " + r.get("error", "failed"))[:190], show_alert=True)

    if sec == "ail":
        models = await app.openrouter.free_models(25)
        return await edit_view(cb, "📃 <b>Verified free models</b>\n<code>" + esc("\n".join(models) or "catalogue unavailable") + "</code>",
                               [[("AI", "a:ai")]])

    if sec in ("aim", "aif"):
        await _state_set(app, uid, a="aimodel" if sec == "aim" else "aifb")
        msg = "Send the OpenRouter model id (must end in <code>:free</code> / be verified free)." if sec == "aim" else \
            "Send fallback model ids separated by commas (free models only). Send <code>-</code> to clear."
        return await edit_view(cb, "🧠 " + msg, [[("Cancel", "a:ai")]])

    # ----------------------------------------------------------------------- TMDB
    if sec == "im":
        h = await app.lookup.probe() if app.lookup.enabled else {"ok": False, "error": "not configured"}
        cached = await app.db[dbm.TITLE_CACHE].count_documents({})
        text = (f"🎞 <b>TMDB</b>\nTitles are identified through the TMDB API (no local database).\n\n"
                f"Status: {'✅ working' if h['ok'] else '❌ ' + esc(h.get('error') or 'no results')}\n"
                f"Cached lookups: {cached}" + (f"\nLatency: {h['latency_ms']} ms" if h.get("latency_ms") is not None else ""))
        return await edit_view(cb, text, [[("Test again", "a:im"), ("Clear lookup cache", "a:imc")], *BACK])

    if sec == "imc":
        r = await app.db[dbm.TITLE_CACHE].delete_many({})
        return await cb.answer(f"Cleared {r.deleted_count} cached lookups", show_alert=True)

    # ---------------------------------------------------------------------- users
    if sec == "us":
        u = await app.users.list(1, 8)
        banned = await app.db[dbm.USERS].count_documents({"banned": True})
        lines = "\n".join(f"• <code>{x['id']}</code> {esc(x.get('username') or x.get('first_name') or '')}{' 🚫' if x.get('banned') else ''}"
                          for x in u["items"])
        rows = [[("User info", "a:usi"), ("Ban", "a:usb"), ("Unban", "a:usu")], *BACK]
        return await edit_view(cb, f"👥 <b>Users</b>\nTotal {u['total']} · banned {banned}\n\n{lines}", rows)

    if sec in ("usb", "usu", "usi"):
        await _state_set(app, uid, a={"usb": "ban", "usu": "unban", "usi": "uinfo"}[sec])
        return await edit_view(cb, "Send the numeric Telegram user id.", [[("Cancel", "a:us")]])

    # ------------------------------------------------------------------- settings
    if sec == "se":
        cfg = await app.openrouter.config()
        text = (f"⚙️ <b>Settings</b>\nCache backend: <b>{app.cache.backend}</b>\nAI enabled: {cfg['enabled']}\n"
                f"Auto-approve ≥ {app.settings.match_auto_threshold} · review ≥ {app.settings.match_review_threshold}\n"
                f"Token TTL: {app.settings.token_ttl_seconds}s · Rate limit: {app.settings.rate_limit_per_minute}/min\n"
                f"Auto-delete: {app.settings.delivery_autodelete_minutes or 'off'} min\n\n"
                "Environment-level settings are changed in your deployment config.")
        return await edit_view(cb, text, [[("Clear cache", "a:sec")], *BACK])

    if sec == "web":
        from ..site import set_site_config, site_config
        if len(p) > 2 and p[2] in ("p", "s"):
            cur = await site_config(app)
            if p[2] == "p":
                await set_site_config(app, public=not cur["public"])
            else:
                await set_site_config(app, streaming=not cur["streaming"])
            log.info("site config changed from Telegram by %s", uid)
        cfg = await site_config(app)
        text = (f"🌐 <b>Website & streaming</b>\n\nPublic website: {'✅ on' if cfg['public'] else '⛔ off'}\n"
                f"Streaming: {'✅ on' if cfg['streaming'] else '⛔ off'}\n"
                f"Public URL: {cfg['base_url'] or 'not set'}\n\n"
                "Streaming sends video through your server. Switches apply immediately.")
        rows = [[("Turn website off" if cfg["public"] else "Turn website on", "a:web:p")],
                [("Turn streaming off" if cfg["streaming"] else "Turn streaming on", "a:web:s")],
                [("Refresh", "a:web")], *BACK]
        return await edit_view(cb, text, rows)

    if sec == "sec":
        n = await app.cache.clear()
        await app.catalog.bump()
        return await cb.answer(f"Cache cleared ({n} entries)", show_alert=True)

    # --------------------------------------------------------------------- broadcast
    if sec == "bc":
        counts = await app.chats.counts()
        items = await app.broadcasts.listing(8)
        lines = [f"<b>Broadcast</b>\n\nReachable users: <b>{counts['users']}</b>\nGroups: <b>{counts['groups']}</b>\n"]
        rows = []
        for b in items:
            st = b["stats"]
            lines.append(f"{b['status']}: {esc(b['text'][:30])} - sent {st['sent']}, failed {st['failed']}, unreachable {st['unreachable']}")
            rows.append([(f"{b['status']}: {b['text'][:28]}", f"a:bcv:{b['_id']}")])
        if not items:
            lines.append("No broadcasts yet.")
        rows += [[("New broadcast", "a:bcn")], *BACK]
        return await edit_view(cb, "\n".join(lines), rows)

    if sec == "bcn":
        await _state_set(app, uid, a="bcnew")
        return await edit_view(cb, "<b>New broadcast</b>\nSend one message in this format:\n"
                                   "<code>text | image https URL or - | button label or - | button https URL or -</code>\n"
                                   "Messages with an image are limited to 1000 characters.", [[("Cancel", "a:bc")]])

    if sec in ("bcv", "bca", "bct", "bcs", "bcc", "bcd") and len(p) > 2:
        bid = p[2]
        if sec == "bca" and len(p) > 3 and p[3] in ("users", "groups", "all"):
            from ..broadcast import _oid
            await app.db[dbm.BROADCASTS].update_one({"_id": _oid(bid), "status": "draft"}, {"$set": {"audience": p[3]}})
        try:
            if sec == "bct":
                await app.broadcasts.test(bid, uid)
                await cb.answer("Test message sent to you.", show_alert=True)
            elif sec == "bcs":
                await app.broadcasts.start(bid)
            elif sec == "bcc":
                await app.broadcasts.cancel(bid)
            elif sec == "bcd":
                await app.broadcasts.delete(bid)
                return await edit_view(cb, "Deleted.", [[("Broadcast", "a:bc")]])
        except Exception as exc:
            from ..broadcast import BroadcastError
            if not isinstance(exc, BroadcastError):
                raise
            await cb.answer(str(exc)[:190], show_alert=True)
        b = await app.broadcasts.get(bid)
        if not b:
            return await edit_view(cb, "Broadcast not found.", [[("Broadcast", "a:bc")]])
        st = b["stats"]
        text = (f"<b>Broadcast</b> - {b['status']}\nAudience: <b>{b['audience']}</b>\n\n{esc(b['text'][:500])}\n\n"
                f"Sent {st['sent']} · failed {st['failed']} · unreachable {st['unreachable']}"
                + (f" of {b['total']}" if b.get("total") else ""))
        rows = []
        if b["status"] == "draft":
            rows += [[("Users", f"a:bca:{bid}:users"), ("Groups", f"a:bca:{bid}:groups"), ("All", f"a:bca:{bid}:all")],
                     [("Send test to me", f"a:bct:{bid}")], [("Start broadcast", f"a:bcs:{bid}")]]
        if b["status"] in ("queued", "running"):
            rows += [[("Stop", f"a:bcc:{bid}")], [("Refresh", f"a:bcv:{bid}")]]
        else:
            rows += [[("Delete", f"a:bcd:{bid}")]]
        rows.append([("Broadcast", "a:bc")])
        return await edit_view(cb, text, rows)

    # --------------------------------------------------------------------- ads
    if sec == "ad":
        if len(p) > 2 and p[2] == "t":
            await app.ads.set_enabled(not await app.ads.enabled())
        on = await app.ads.enabled()
        items = await app.ads.listing()
        rows = [[("Turn ads off" if on else "Turn ads on", "a:ad:t")]]
        for a in items[:10]:
            rows.append([(f"{'On' if a.get('active') else 'Off'}: {a['title'][:30]}", f"a:adv:{a['_id']}")])
        rows += [[("New ad", "a:adn")], *BACK]
        return await edit_view(cb, f"<b>Ads</b>\nAds are {'on' if on else 'off'}. Every ad is labelled Sponsored.\n"
                                   f"{len(items)} ad(s).", rows)

    if sec == "adn":
        await _state_set(app, uid, a="adnew")
        return await edit_view(cb, "<b>New ad</b>\nSend one message in this format:\n"
                                   "<code>title | https link | image https URL or - | text or - | where</code>\n"
                                   "where: any of <code>results</code>, <code>delivery</code>, <code>site</code> separated by commas.",
                               [[("Cancel", "a:ad")]])

    if sec in ("adv", "adt", "add") and len(p) > 2:
        ad_id = p[2]
        if sec == "adt":
            cur = await app.ads.get(ad_id)
            if cur:
                await app.ads.set_active(ad_id, not cur.get("active"))
        if sec == "add":
            await app.ads.delete(ad_id)
            return await edit_view(cb, "Deleted.", [[("Ads", "a:ad")]])
        a = await app.ads.get(ad_id)
        if not a:
            return await edit_view(cb, "Ad not found.", [[("Ads", "a:ad")]])
        ctr = f"{round(100 * a['clicks'] / a['impressions'], 1)}%" if a.get("impressions") else "-"
        text = (f"<b>{esc(a['title'])}</b>\n{esc(a['link_url'])}\nImage: {esc(a.get('image_url') or 'none')}\n"
                f"Where: {', '.join(a['placements'])}\nStatus: {'on' if a['active'] else 'off'}\n"
                f"Impressions {a['impressions']} · clicks {a['clicks']} · click rate {ctr}")
        return await edit_view(cb, text, [[("Turn off" if a["active"] else "Turn on", f"a:adt:{ad_id}")],
                                          [("Delete", f"a:add:{ad_id}")], [("Ads", "a:ad")]])

    # --------------------------------------------------------------------- poster
    if sec == "ps":
        await _state_set(app, uid, a="poster_id")
        return await edit_view(cb, "🖼 Send the <b>title id</b> (e.g. <code>tt1375666</code>), then an <b>https image URL</b> for its poster.",
                               [[("Cancel", "a:home")]])


# ================================================================= text input
async def handle_text_input(app: App, client, msg) -> bool:
    """Returns True when the message was consumed by an admin prompt."""
    uid = msg.from_user.id
    st = await _state_get(app, uid)
    if not st:
        return False
    role = await app.users.telegram_role(uid)
    if role is None:
        await _state_clear(app, uid)
        return False
    actor = {"type": "telegram", "id": uid}
    a = st["a"]
    text = (msg.text or "").strip()
    try:
        if a == "bcnew" and role_allows(role, "broadcast"):
            from ..broadcast import BroadcastError
            bits = [b.strip() for b in text.split("|")]
            bits += ["-"] * (4 - len(bits))
            blank = lambda v: "" if v in ("", "-") else v
            try:
                b = await app.broadcasts.create({"text": bits[0], "image_url": blank(bits[1]), "button_text": blank(bits[2]),
                                                 "button_url": blank(bits[3]), "audience": "users"}, f"tg:{uid}")
            except BroadcastError as exc:
                await msg.reply_text(f"{esc(exc)}\nTry again or cancel.")
                return True
            await _state_clear(app, uid)
            await msg.reply_text("Draft saved. Choose who gets it, send yourself a test, then start.",
                                 reply_markup=to_markup([[("Open draft", f"a:bcv:{b['_id']}")]]))
            return True

        if a == "adnew" and role_allows(role, "ads"):
            from ..ads import AdError
            bits = [b.strip() for b in text.split("|")]
            bits += ["-"] * (5 - len(bits))
            blank = lambda v: "" if v in ("", "-") else v
            names = {"results": "bot_results", "delivery": "bot_delivery", "site": "site_banner"}
            places = [names[x.strip()] for x in bits[4].split(",") if x.strip() in names]
            try:
                ad = await app.ads.create({"title": bits[0], "link_url": bits[1], "image_url": blank(bits[2]),
                                           "text": blank(bits[3]), "placements": places}, f"tg:{uid}")
            except AdError as exc:
                await msg.reply_text(f"{esc(exc)}\nTry again or cancel.")
                return True
            await _state_clear(app, uid)
            await msg.reply_text("Ad saved and live.", reply_markup=to_markup([[("Open ad", f"a:adv:{ad['_id']}")]]))
            return True

        if a == "srcadd" and role_allows(role, "sources"):
            ref: Any = None
            fwd = getattr(msg, "forward_from_chat", None)
            if fwd is not None:
                ref = fwd.id
            else:
                m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,32})", text)
                ref = int(text) if re.fullmatch(r"-?\d{5,15}", text) else (m.group(1) if m else None)
            if ref is None:
                await msg.reply_text("Could not read a channel from that. Try again or cancel.")
                return True
            try:
                src = await app.indexer.add_source(ref, uid)
            except Exception as exc:
                await msg.reply_text(f"❌ {esc(str(exc)[:300])}")
                return True
            await _state_clear(app, uid)
            await msg.reply_text(f"✅ Added <b>{esc(src['title'])}</b>.", reply_markup=to_markup([[("Sources", "a:src")]]))
            return True

        if a == "rvsearch" and role_allows(role, "review"):
            ym = re.search(r"\b((?:19|20)\d{2})\b", text)
            year = int(ym.group(1)) if ym else None
            q = re.sub(r"\b(?:19|20)\d{2}\b", "", text).strip() if ym else text
            res = await app.review.search_titles(q, year)
            if not res:
                await msg.reply_text("No results. Try another spelling or use a local title.")
                return True
            await app.cache.set(f"rvs:{uid}", res, ttl=STATE_TTL)
            fid = st["fid"]
            rows = [[(f"{c['title'][:30]} ({c.get('year') or '?'}) {c['kind']}", f"a:rvq:{i}:{fid}")] for i, c in enumerate(res[:8])]
            rows.append([("Review", "a:rv")])
            await _state_clear(app, uid)
            await msg.reply_text("Pick the correct title:", reply_markup=to_markup(rows))
            return True

        if a == "rvlocal" and role_allows(role, "review"):
            bits = [b.strip() for b in text.split("|")]
            if len(bits) != 3 or bits[2].lower() not in ("movie", "series") or not (bits[1].isdigit() or bits[1] in ("", "-")):
                await msg.reply_text("Format: <b>Title | year | movie</b>")
                return True
            await app.review.approve_local(st["fid"], bits[0], int(bits[1]) if bits[1].isdigit() else None, bits[2].lower(), actor)
            await _state_clear(app, uid)
            await msg.reply_text("✅ Saved.", reply_markup=to_markup([[("Next", "a:rv")]]))
            return True

        if a in ("aimodel", "aifb") and role_allows(role, "ai"):
            try:
                if a == "aimodel":
                    await app.openrouter.set_config(model=text)
                else:
                    await app.openrouter.set_config(fallbacks=[] if text == "-" else [x.strip() for x in text.split(",") if x.strip()])
            except ValueError as exc:
                await msg.reply_text(f"❌ {esc(exc)}")
                return True
            await _state_clear(app, uid)
            await msg.reply_text("✅ Saved.", reply_markup=to_markup([[("AI", "a:ai")]]))
            return True

        if a in ("ban", "unban", "uinfo") and role_allows(role, "users"):
            if not re.fullmatch(r"\d{1,15}", text):
                await msg.reply_text("Send a numeric user id.")
                return True
            target = int(text)
            if a == "ban":
                ok = await app.users.ban(target, "banned from Telegram panel")
                out = "🚫 Banned." if ok else "That user cannot be banned."
            elif a == "unban":
                out = "✅ Unbanned." if await app.users.unban(target) else "User not found."
            else:
                u = await app.users.get(target)
                if not u:
                    out = "User not found."
                else:
                    act = await app.users.activity(target, 5)
                    out = (f"👤 <code>{target}</code> {esc(u.get('username') or '')}\nBanned: {u.get('banned')}\n"
                           f"Searches: {u.get('search_count', 0)} · Deliveries: {u.get('delivery_count', 0)}\n"
                           f"Last: {', '.join(esc(s['query']) for s in act['searches']) or '—'}")
            await _state_clear(app, uid)
            await msg.reply_text(out, reply_markup=to_markup([[("Users", "a:us")]]))
            return True

        if a == "poster_id" and role_allows(role, "media_edit"):
            if not re.fullmatch(r"(?:tt\d{7,10}|loc_[0-9a-f]{10})", text) or not await app.catalog.get_title(text):
                await msg.reply_text("Unknown title id. Send an id that exists in the catalog.")
                return True
            await _state_set(app, uid, a="poster_url", tid=text)
            await msg.reply_text("Now send the poster image URL (https://…). Send <code>auto</code> to fetch it from TMDB.")
            return True

        if a == "poster_url" and role_allows(role, "media_edit"):
            from ..tmdb import valid_image_url
            tid = st["tid"]
            if text.lower() == "auto":
                res = await app.posters.refresh_title(tid, force=True)
                reply = {"set": "✅ Poster fetched from TMDB.", "none": "TMDB has no confident match for this title.",
                         "disabled": "TMDB is not configured (set TMDB_API_KEY)."}.get(res, "Title not found.")
            elif valid_image_url(text):
                await app.catalog.set_poster(tid, text.strip(), "manual")
                await app.db[dbm.CORRECTIONS].insert_one({"type": "poster", "actor": actor, "imdb_id": tid,
                                                          "poster": text.strip(), "active": True, "created_at": datetime.now(timezone.utc)})
                reply = "✅ Poster saved."
            else:
                await msg.reply_text("Send a direct https:// link to an image, or <code>auto</code>.")
                return True
            await _state_clear(app, uid)
            await msg.reply_text(reply, reply_markup=to_markup([[("Admin", "a:home")]]))
            return True
    except Exception as exc:
        log.error("admin input failed: %s", exc)
        await msg.reply_text("⚠️ Something went wrong. Please try again.")
        return True
    return False
