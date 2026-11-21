"""Telegram client creation and user-facing handlers.

All user interaction is button-driven. The only command is /start (which also
carries delivery deep links). Admin features live in `admin.py`.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import os
from typing import Any

from .. import brand
from ..app import App
from . import ui
from .ui import parse_cb, to_markup

log = logging.getLogger("ajcine.bot")


def create_clients(settings) -> tuple[Any, Any | None]:
    from pyrogram import Client

    bot = Client("ajcine-bot", api_id=settings.api_id, api_hash=settings.api_hash,
                 bot_token=settings.bot_token, in_memory=True, parse_mode=_html())
    user = None
    if settings.session_string:
        user = Client("ajcine-user", api_id=settings.api_id, api_hash=settings.api_hash,
                      session_string=settings.session_string, in_memory=True, no_updates=False)
    return bot, user


def _html():
    from pyrogram import enums
    return enums.ParseMode.HTML


def safe(fn):
    """Handler wrapper: a failing handler must never crash the update loop."""
    @functools.wraps(fn)
    async def wrapper(client, update, *a, **kw):
        try:
            return await fn(client, update, *a, **kw)
        except Exception as exc:
            name = type(exc).__name__
            if name in ("MessageNotModified", "QueryIdInvalid"):
                return
            log.error("handler %s failed: %s: %s", fn.__name__, name, str(exc)[:200])
            try:
                if hasattr(update, "answer"):
                    await update.answer("⚠️ Temporary problem. Please try again.", show_alert=False)
                elif hasattr(update, "reply_text"):
                    await update.reply_text("⚠️ Temporary problem. Please try again in a moment.")
            except Exception:
                pass
    return wrapper


def home_rows(is_admin: bool) -> list[list[tuple[str, str]]]:
    rows = [[("Recently added", "h:recent"), ("Popular", "h:pop")],
            [("My list", "h:list"), ("My searches", "h:hist")],
            [("Request a title", "h:req"), ("Help", "h:help"), ("About", "h:about")]]
    if is_admin:
        rows.append([("Admin panel", "a:home")])
    return rows


async def edit_view(cb_or_msg: Any, text: str, rows: list, photo: str | None = None) -> None:
    """Edit the current message in place (text or caption). Never sends a new message
    unless the media type must change."""
    markup = to_markup(rows)
    msg = getattr(cb_or_msg, "message", cb_or_msg)
    try:
        if getattr(msg, "photo", None):
            await msg.edit_caption(text[:1024], reply_markup=markup)
        else:
            await msg.edit_text(text[:4000], reply_markup=markup, disable_web_page_preview=True)
    except Exception as exc:
        if type(exc).__name__ != "MessageNotModified":
            raise


def _private_force_sub_links() -> dict[str, str]:
    """Read private-channel invite links from AJ_FORCE_SUB_INVITE_LINKS JSON.

    Example:
      AJ_FORCE_SUB_INVITE_LINKS={"-1001234567890":"https://t.me/+inviteCode"}

    Keys are Telegram channel IDs. Values must be the actual invite URLs.
    """
    import json

    raw = os.getenv("AJ_FORCE_SUB_INVITE_LINKS", "{}").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("AJ_FORCE_SUB_INVITE_LINKS must contain valid JSON")
        return {}
    if not isinstance(data, dict):
        log.error("AJ_FORCE_SUB_INVITE_LINKS must be a JSON object")
        return {}

    links: dict[str, str] = {}
    for channel_id, invite_url in data.items():
        channel_id = str(channel_id).strip()
        invite_url = str(invite_url).strip()
        if channel_id and invite_url.startswith(("https://t.me/+", "https://t.me/joinchat/")):
            links[channel_id] = invite_url
        else:
            log.warning("Ignoring invalid private invite link for channel %r", channel_id)
    return links


async def _force_sub_join_link(client: Any, channel: Any,
                               private_links: dict[str, str]) -> tuple[str | None, str]:
    """Resolve public usernames and private invite URLs for force-sub channels.

    `missing_subscriptions()` currently supplies channel identifiers. Public
    usernames and numeric IDs are both accepted. A private numeric ID needs an
    invite URL configured in AJ_FORCE_SUB_INVITE_LINKS.
    """
    value = str(channel).strip()
    if not value:
        return None, "required channel"

    # An invite URL supplied directly by configuration can be used as-is.
    if value.startswith(("https://t.me/+", "https://t.me/joinchat/")):
        return value, "private channel"

    # Accept public t.me links and @usernames.
    if value.startswith("https://t.me/"):
        return value, value.rstrip("/").rsplit("/", 1)[-1]
    if value.startswith("@"):
        value = value[1:]
    if not value.lstrip("-").isdigit():
        username = value.removeprefix("t.me/").strip("/")
        if username and all(ch.isalnum() or ch == "_" for ch in username):
            return f"https://t.me/{username}", f"@{username}"
        return None, value or "required channel"

    # Numeric ID: resolve public channels through Telegram when possible.
    channel_id = value
    try:
        chat = await client.get_chat(int(channel_id))
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}", f"@{chat.username}"
    except Exception as exc:
        log.debug("Could not resolve force-sub channel %s: %s", channel_id, type(exc).__name__)

    # Private channels have no public username; use the administrator's invite URL.
    invite_url = private_links.get(channel_id)
    if invite_url:
        return invite_url, "private channel"
    return None, "private channel"


def register_handlers(app: App, bot: Any, user_client: Any | None = None) -> None:
    from pyrogram import filters
    from pyrogram.handlers import CallbackQueryHandler, MessageHandler

    from . import admin as admin_mod

    s = app.settings
    bot_username: dict[str, str] = {}

    async def username() -> str:
        if "n" not in bot_username:
            bot_username["n"] = (await bot.get_me()).username
        return bot_username["n"]

    async def guard(update: Any, tg_user: Any) -> bool:
        """Common access checks. Returns True when the user may continue."""
        await app.users.touch(tg_user)
        if await app.users.is_banned(tg_user.id):
            if hasattr(update, "answer"):
                await update.answer("Your access has been disabled.", show_alert=True)
            else:
                await update.reply_text("🚫 Your access has been disabled.")
            return False
        if await app.cache.incr(f"act:{tg_user.id}", 60) > s.rate_limit_per_minute * 3:
            if hasattr(update, "answer"):
                await update.answer("Slow down a little.", show_alert=False)
            return False
        return True

    async def is_admin(uid: int) -> bool:
        return (await app.users.telegram_role(uid)) is not None

    # ---------------------------------------------------------------- /start
    @safe
    async def on_start(client, msg):
        if not await guard(msg, msg.from_user):
            return
        arg = msg.command[1] if len(msg.command) > 1 else ""
        if arg.startswith("dl_"):
            return await handle_delivery(client, msg, arg[3:])
        await msg.reply_text(brand.START_TEXT, reply_markup=to_markup(home_rows(await is_admin(msg.from_user.id))))

    async def handle_delivery(client, msg, token: str):
        from ..delivery import DeliveryError
        uid = msg.from_user.id
        try:
            miss = await app.delivery.missing_subscriptions(client, uid) if s.force_sub_channels else []
            if miss:
                rows = []
                private_links = _private_force_sub_links()

                for channel in miss:
                    join_url, label = await _force_sub_join_link(client, channel, private_links)
                    if join_url:
                        rows.append([(f"Join {label}"[:64], "url:" + join_url)])
                    else:
                        log.warning("No join URL available for required channel %r", channel)

                rows.append([
                    ("I joined — try again",
                     "url:https://t.me/" + await username() + "?start=dl_" + token)
                ])
                message = "Please join the required channel(s) first, then try again."
                if len(rows) == 1:
                    message += "\n\nA channel invite link is not configured. Please contact the administrator."
                return await msg.reply_text(message, reply_markup=to_markup(rows))
            note = await msg.reply_text("📤 Sending your file…")
            report = await app.delivery.deliver(client, token, uid)
        except DeliveryError as exc:
            return await msg.reply_text(f"⚠️ {exc.message}")
        try:
            await note.delete()
        except Exception:
            pass
        if report["sent"] == 0:
            return await msg.reply_text("😕 This file is no longer available. An administrator has been notified; "
                                        "please try another quality or language.")
        extra = f"\n⏳ Files are deleted after {report['autodelete_minutes']} minutes. Forward them to save." \
            if report["autodelete_minutes"] else ""
        miss_note = f"\n⚠️ {report['missing']} file(s) were unavailable." if report["missing"] else ""
        await msg.reply_text(f"✅ Sent {report['sent']} file(s).{miss_note}{extra}\n\n<i>{brand.FOOTER}</i>")
        await send_delivery_ad(msg, uid)

    async def send_delivery_ad(msg, uid: int):
        """One labelled sponsored message after a delivery, at most once per cooldown per user."""
        try:
            ad = await app.ads.pick("bot_delivery")
            if not ad or not await app.ads.bot_cooldown_ok(uid):
                return
            text = f"<i>Sponsored</i>\n<b>{ui.esc(ad['title'])}</b>" + (f"\n{ui.esc(ad['text'])}" if ad.get("text") else "")
            markup = to_markup([[(ad["button_text"], "url:" + app.ads.link_for(ad))]])
            if ad.get("image_url"):
                try:
                    await msg.reply_photo(ad["image_url"], caption=text[:1024], reply_markup=markup)
                except Exception as exc:
                    log.warning("ad image failed: %s", type(exc).__name__)
                    await msg.reply_text(text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup)
            await app.ads.impression(ad["_id"])
        except Exception as exc:
            log.warning("delivery ad skipped: %s", type(exc).__name__)

    # ---------------------------------------------------------------- search
    async def query_token(params: dict) -> str:
        import json
        tok = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
        await app.cache.set("qt:" + tok, params, ttl=3600)
        return tok

    async def render_results(params: dict, page: int, user_id: int | None, record: bool):
        res = await app.search.search(params["q"], user_id=user_id, kind=params.get("k"), language=params.get("l"),
                                      page=page, record=record)
        tok = await query_token(params)
        rows = ui.results_keyboard(res, tok)
        if not res["items"]:
            rows = ui.suggestions_keyboard(res)
            if user_id is not None and len(params["q"]) >= 2:
                rows.append([("Request this title", f"rq:{tok}")])
        else:
            ad = await app.ads.pick("bot_results")
            if ad:
                await app.ads.impression(ad["_id"])
                rows.append([(("Sponsored: " + ad["title"])[:60], "url:" + app.ads.link_for(ad))])
        return res, tok, rows

    @safe
    async def on_text(client, msg):
        if not msg.text or msg.text.startswith("/"):
            return
        uid = msg.from_user.id
        # admin conversational input takes priority in private chats
        if msg.chat.type.name == "PRIVATE" and await admin_mod.handle_text_input(app, client, msg):
            return
        if not await guard(msg, msg.from_user):
            return
        if not await app.users.can(uid, "search"):
            return await msg.reply_text("Search is not enabled for your account.")
        q = msg.text.strip()
        in_group = msg.chat.type.name != "PRIVATE"
        if in_group:
            await app.chats.track(msg.chat)
        if in_group and (not s.group_search or len(q) < 3 or len(q) > 60):
            return
        await app.users.count_activity(uid, "search_count")
        res, tok, rows = await render_results({"q": q[:100]}, 1, uid, record=True)
        if in_group and not res["items"]:
            return  # stay quiet in groups when nothing matches
        text = ui.results_text(res)
        if not res["items"]:
            text += "\nCheck the spelling or try the original title."
        await msg.reply_text(text, reply_markup=to_markup(rows) if rows else None, quote=in_group)

    # ---------------------------------------------------------------- navigation
    async def show_title(cb, title_id: str):
        doc = await app.catalog.get_title(title_id)
        if not doc or not doc.get("file_count"):
            return await cb.answer("This title is no longer available.", show_alert=True)
        if doc["kind"] == "series":
            seasons = await app.catalog.seasons_of(title_id)
            rows = ui.seasons_keyboard(title_id, seasons, await app.catalog.has_complete_pack(title_id))
            langs = doc.get("languages")
            text = ui.title_card(doc, langs) + "\n\n<b>Choose a season</b>"
        else:
            langs_data = await app.catalog.languages_for(title_id)
            rows = ui.language_keyboard(title_id, langs_data, f"l:{title_id}:{{lang}}", "r:back")
            rows = [r for r in rows if r[0][1] != "r:back"]
            text = ui.title_card(doc, [l["code"] for l in langs_data]) + "\n\n<b>Choose a language</b>"
        rows = list(rows) + [[await watch_toggle_row(cb.from_user.id, title_id)]]
        await present(cb, doc, text, rows)

    async def watch_toggle_row(uid: int, title_id: str):
        saved = await app.watchlist.has(uid, title_id)
        return ("Remove from my list" if saved else "Add to my list", f"w:{title_id}")

    async def present(cb, doc: dict, text: str, rows: list):
        """Show a title card with its poster when one exists; edit in place otherwise."""
        from ..tmdb import poster_src
        src = poster_src(doc.get("poster"))
        has_photo = bool(getattr(cb.message, "photo", None))
        if src and src.startswith("/posters/"):      # legacy poster saved as a local file by an earlier version
            local = os.path.join(s.poster_dir, os.path.basename(src))
            src = local if os.path.exists(local) else None
        if src and not has_photo:
            try:
                await cb.message.reply_photo(src, caption=text[:1024], reply_markup=to_markup(rows))
                try:
                    await cb.message.delete()
                except Exception:
                    pass
                return
            except Exception as exc:
                log.warning("poster send failed: %s", type(exc).__name__)
        await edit_view(cb, text if (has_photo or src) else "🎬 " + text, rows)

    async def deliver_link(cb, title_id: str, files: list[dict], label: str):
        if not files:
            return await cb.answer("No file available for that selection.", show_alert=True)
        if not await app.users.can(cb.from_user.id, "download"):
            return await cb.answer("Downloads are not enabled for your account.", show_alert=True)
        from ..delivery import DeliveryError
        try:
            token = await app.delivery.create_token(cb.from_user.id, [f["_id"] for f in files], title_id)
        except DeliveryError as exc:
            return await cb.answer(exc.message, show_alert=True)
        url = f"https://t.me/{await username()}?start=dl_{token}"
        total = sum(f.get("size") or 0 for f in files)
        from ..catalog import human_size
        text = (f"✅ <b>Ready</b>\n{ui.esc(label)}\n💾 {human_size(total)} · {len(files)} file(s)\n\n"
                f"Tap the button to receive it in your private chat with me.\n"
                f"<i>The link works only for you and expires in {s.token_ttl_seconds // 60} min.</i>")
        rows = [[("Get file in private chat", "url:" + url)]]
        watch = await watch_url(title_id, files)
        if watch:
            rows.insert(0, [("Watch online", "url:" + watch)])
        rows.append([("Back", f"t:{title_id}")])
        await edit_view(cb, text, rows)
        await cb.answer()

    async def watch_url(title_id: str, files: list[dict]) -> str | None:
        """A website player link for a single file, only when streaming is on and the site has a public URL."""
        if len(files) != 1 or not s.public_base_url:
            return None
        from ..site import site_config
        if not (await site_config(app))["streaming"]:
            return None
        f = files[0]
        if f.get("is_season_pack") or f.get("is_complete_series"):
            return None
        token = await app.delivery.create_stream_token(f["_id"], title_id)
        return f"{s.public_base_url}/watch/{token}"

    async def files_by_quality(title_id, lang, q, season=None, episode=None, pack=None):
        files = await app.catalog.files_for(title_id, lang, None if q == "xx" else q, season, episode, pack)
        return [f for f in files if not f.get("quality")] if q == "xx" else files

    @safe
    async def on_callback(client, cb):
        if not await guard(cb, cb.from_user):
            return
        parsed = parse_cb(cb.data)
        if parsed is None:
            return await cb.answer("This button is no longer valid.", show_alert=False)
        kind, g = parsed
        cat = app.catalog

        if kind == "t":
            await show_title(cb, g[0])
            return await cb.answer()

        if kind == "l":  # movie language -> qualities
            tid, lang = g
            quals = await cat.qualities_for(tid, lang)
            if not quals:
                return await cb.answer("No files for that language.", show_alert=True)
            doc = await cat.get_title(tid)
            rows = ui.quality_keyboard(quals, f"q:{tid}:{lang}:{{q}}", f"t:{tid}")
            from ..parsing.languages import lang_name
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b> · {lang_name(lang)}\n\n<b>Choose a quality</b>", rows)
            return await cb.answer()

        if kind == "q":
            tid, lang, q = g
            doc = await cat.get_title(tid)
            files = await files_by_quality(tid, lang, q)
            from ..parsing.languages import lang_name
            return await deliver_link(cb, tid, files[:1], f"{doc['title']} · {lang_name(lang)} · {q}")

        if kind == "s":
            tid = g[0]
            doc = await cat.get_title(tid)
            rows = ui.seasons_keyboard(tid, await cat.seasons_of(tid), await cat.has_complete_pack(tid))
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b>\n\n<b>Choose a season</b>", rows)
            return await cb.answer()

        if kind == "n":
            tid, season = g[0], int(g[1])
            doc = await cat.get_title(tid)
            eps = await cat.episodes_of(tid, season)
            pack = await cat.has_season_pack(tid, season)
            if not eps and not pack:
                return await cb.answer("No episodes indexed for this season.", show_alert=True)
            titles = ""
            named = [e for e in eps if e.get("title")][:0]
            missing = (await cat.missing_episodes(tid)).get(season)
            note = f"\n⚠️ Missing: {', '.join('E%02d' % n for n in missing[:15])}" if missing else ""
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b> · Season {season}\n{len(eps)} episode(s){note}\n\n<b>Choose an episode</b>",
                            ui.episodes_keyboard(tid, season, eps, pack))
            return await cb.answer()

        if kind == "e":
            tid, season, ep = g[0], int(g[1]), int(g[2])
            langs = await cat.languages_for(tid, season, ep)
            if not langs:
                return await cb.answer("Episode unavailable.", show_alert=True)
            doc = await cat.get_title(tid)
            info = next((e for e in await cat.episodes_of(tid, season) if e["episode"] == ep), {})
            name = f" · {ui.esc(info['title'])}" if info.get("title") else ""
            rows = ui.language_keyboard(tid, langs, f"el:{tid}:{season}:{ep}:{{lang}}", f"n:{tid}:{season}")
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b>\nS{season:02d}E{ep:02d}{name}\n\n<b>Choose a language</b>", rows)
            return await cb.answer()

        if kind == "el":
            tid, season, ep, lang = g[0], int(g[1]), int(g[2]), g[3]
            quals = await cat.qualities_for(tid, lang, season, ep)
            if not quals:
                return await cb.answer("No files for that language.", show_alert=True)
            doc = await cat.get_title(tid)
            rows = ui.quality_keyboard(quals, f"eq:{tid}:{season}:{ep}:{lang}:{{q}}", f"e:{tid}:{season}:{ep}")
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b> S{season:02d}E{ep:02d}\n\n<b>Choose a quality</b>", rows)
            return await cb.answer()

        if kind == "eq":
            tid, season, ep, lang, q = g[0], int(g[1]), int(g[2]), g[3], g[4]
            doc = await cat.get_title(tid)
            files = await files_by_quality(tid, lang, q, season, ep)
            return await deliver_link(cb, tid, files[:1], f"{doc['title']} S{season:02d}E{ep:02d} · {q}")

        if kind in ("sp", "cp"):
            tid = g[0]
            season = int(g[1]) if kind == "sp" else None
            pack = "season" if kind == "sp" else "series"
            langs = await cat.languages_for(tid, season, None, pack=pack)
            if not langs:
                return await cb.answer("Pack unavailable.", show_alert=True)
            doc = await cat.get_title(tid)
            tag = f"s:{season}" if kind == "sp" else "c:0"
            k, sn = tag.split(":")
            rows = ui.language_keyboard(tid, langs, f"pl:{tid}:{k}:{sn}:{{lang}}", f"n:{tid}:{season}" if season else f"s:{tid}")
            label = f"Season {season} pack" if season else "Complete series pack"
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b>\n📦 {label}\n\n<b>Choose a language</b>", rows)
            return await cb.answer()

        if kind == "pl":
            tid, k, sn, lang = g[0], g[1], int(g[2]), g[3]
            pack = "season" if k == "s" else "series"
            quals = await cat.qualities_for(tid, lang, sn if k == "s" else None, None, pack=pack)
            if not quals:
                return await cb.answer("No files for that language.", show_alert=True)
            doc = await cat.get_title(tid)
            rows = ui.quality_keyboard(quals, f"pq:{tid}:{k}:{sn}:{lang}:{{q}}", f"{'sp' if k == 's' else 'cp'}:{tid}" + (f":{sn}" if k == "s" else ""))
            await edit_view(cb, f"<b>{ui.esc(doc['title'])}</b>\n📦 Pack\n\n<b>Choose a quality</b>", rows)
            return await cb.answer()

        if kind == "pq":
            tid, k, sn, lang, q = g[0], g[1], int(g[2]), g[3], g[4]
            pack = "season" if k == "s" else "series"
            doc = await cat.get_title(tid)
            files = await files_by_quality(tid, lang, q, sn if k == "s" else None, None, pack)
            return await deliver_link(cb, tid, files[:5], f"{doc['title']} · {'Season ' + str(sn) if k == 's' else 'Complete series'} pack · {q}")

        if kind == "p":
            page, tok = int(g[0]), g[1]
            params = await app.cache.get("qt:" + tok)
            if not params:
                return await cb.answer("Search expired. Please search again.", show_alert=True)
            res, tok, rows = await render_results(params, page, cb.from_user.id, record=False)
            await edit_view(cb, ui.results_text(res), rows)
            return await cb.answer()

        if kind == "r":
            params = await app.cache.get("qt:" + g[0])
            if not params:
                return await cb.answer("Search expired. Please search again.", show_alert=True)
            res, tok, rows = await render_results(params, 1, cb.from_user.id, record=False)
            await edit_view(cb, ui.results_text(res), rows)
            return await cb.answer()

        if kind == "w":
            added = await app.watchlist.toggle(cb.from_user.id, g[0])
            if added is None:
                return await cb.answer("Your list is full (100 titles). Remove one first.", show_alert=True)
            try:
                kb = cb.message.reply_markup.inline_keyboard if cb.message.reply_markup else []
                rows = []
                for line in kb:
                    row = []
                    for b in line:
                        if b.callback_data == f"w:{g[0]}":
                            row.append(("Remove from my list" if added else "Add to my list", f"w:{g[0]}"))
                        elif b.callback_data:
                            row.append((b.text, b.callback_data))
                        elif b.url:
                            row.append((b.text, "url:" + b.url))
                    rows.append(row)
                await cb.edit_message_reply_markup(to_markup(rows))
            except Exception as exc:
                log.debug("watchlist keyboard refresh skipped: %s", type(exc).__name__)
            return await cb.answer("Saved to your list." if added else "Removed from your list.")

        if kind == "rq":
            params = await app.cache.get("qt:" + g[0])
            if not params:
                return await cb.answer("Search expired. Please search again.", show_alert=True)
            from ..requests import RequestError
            try:
                r = await app.requests.submit(params["q"], None, None, f"tg:{cb.from_user.id}", s.web_secret_key)
            except RequestError as exc:
                return await cb.answer(exc.message, show_alert=True)
            msg = {"created": "Request sent. Votes: 1.", "voted": f"Added your vote. Votes: {r.get('votes')}.",
                   "already": "You already requested this title.",
                   "exists": f"{r.get('title')} is already available. Search for it."}[r["status"]]
            return await cb.answer(msg, show_alert=True)

    async def list_view(uid: int):
        titles = await app.watchlist.titles(uid, app.catalog)
        rows = [[(f"{t['title']}" + (f" ({t['year']})" if t.get("year") else ""), f"t:{t['_id']}")] for t in titles]
        return titles, rows

    async def show_list(target, uid: int):
        titles, rows = await list_view(uid)
        if not titles:
            return await target("Your list is empty. Open a title and tap <b>Add to my list</b>.", [[("Menu", "h:menu")]])
        rows.append([("Menu", "h:menu")])
        await target("<b>My list</b>", rows)

    # ---------------------------------------------------------------- home menu
    @safe
    async def on_home(client, cb):
        if not await guard(cb, cb.from_user):
            return
        action = cb.data.split(":", 1)[1]
        if action == "about":
            return await edit_view(cb, brand.ABOUT_TEXT, [[("Menu", "h:menu")]]) or await cb.answer()
        if action == "help":
            await edit_view(cb, brand.HELP_TEXT, [[("Menu", "h:menu")]])
            return await cb.answer()
        if action == "req":
            await edit_view(cb, "<b>Request a title</b>\nSend me the name of the movie or series. "
                                "If it is not in the library you will get a <b>Request this title</b> button.",
                            [[("Menu", "h:menu")]])
            return await cb.answer()
        if action == "list":
            async def target(text, rows):
                await edit_view(cb, text, rows)
            await show_list(target, cb.from_user.id)
            return await cb.answer()
        if action == "menu":
            await edit_view(cb, brand.START_TEXT, home_rows(await is_admin(cb.from_user.id)))
            return await cb.answer()
        if action in ("recent", "pop"):
            items = await (app.search.recent() if action == "recent" else app.search.popular())
            if not items:
                return await cb.answer("Nothing here yet.", show_alert=True)
            rows = [[(f"{i['title']}" + (f" ({i['year']})" if i.get('year') else ""), f"t:{i['id']}")]
                    for i in items]
            rows.append([("Menu", "h:menu")])
            await edit_view(cb, "🆕 <b>Recently added</b>" if action == "recent" else "🔥 <b>Popular right now</b>", rows)
            return await cb.answer()
        if action == "hist":
            hist = await app.search.history(cb.from_user.id)
            if not hist:
                return await cb.answer("No searches yet.", show_alert=True)
            rows = []
            for h in hist:
                tok = await query_token({"q": h})
                rows.append([(f"{h[:40]}", f"r:{tok}")])
            rows.append([("Menu", "h:menu")])
            await edit_view(cb, "🕘 <b>Your recent searches</b>", rows)
            return await cb.answer()
        await cb.answer()

    # ---------------------------------------------------------------- commands
    @safe
    async def on_help(client, msg):
        if await guard(msg, msg.from_user):
            await msg.reply_text(brand.HELP_TEXT, reply_markup=to_markup([[("Menu", "h:menu")]]))

    @safe
    async def on_mylist(client, msg):
        if not await guard(msg, msg.from_user):
            return
        async def target(text, rows):
            await msg.reply_text(text, reply_markup=to_markup(rows))
        await show_list(target, msg.from_user.id)

    @safe
    async def on_listing(client, msg):
        if not await guard(msg, msg.from_user):
            return
        trending = msg.command[0] == "trending"
        items = await (app.search.popular() if trending else app.search.recent())
        if not items:
            return await msg.reply_text("Nothing here yet.")
        rows = [[(i["title"] + (f" ({i['year']})" if i.get("year") else ""), f"t:{i['id']}")] for i in items]
        await msg.reply_text("<b>Trending</b>" if trending else "<b>Recently added</b>", reply_markup=to_markup(rows))

    @safe
    async def on_my_chat_member(client, update):
        """Keep the list of groups the bot is in (used for group broadcasts)."""
        new = update.new_chat_member
        chat = update.chat
        if new is None or getattr(new.user, "id", None) != (await client.get_me()).id:
            return
        status = getattr(getattr(new, "status", None), "name", "")
        if status in ("LEFT", "BANNED"):
            await app.chats.left(chat.id)
        elif status in ("MEMBER", "ADMINISTRATOR", "OWNER"):
            await app.chats.track(chat, force=True)

    bot.add_handler(MessageHandler(on_start, filters.command("start") & filters.private))
    bot.add_handler(MessageHandler(on_help, filters.command("help") & (filters.private | filters.group)))
    bot.add_handler(MessageHandler(on_mylist, filters.command("mylist") & filters.private))
    bot.add_handler(MessageHandler(on_listing, filters.command(["trending", "recent"]) & (filters.private | filters.group)))
    from pyrogram.handlers import ChatMemberUpdatedHandler
    bot.add_handler(ChatMemberUpdatedHandler(on_my_chat_member))
    bot.add_handler(MessageHandler(on_text, filters.text & ~filters.command(["start", "help", "mylist", "trending", "recent"]) & (filters.private | filters.group)))
    bot.add_handler(CallbackQueryHandler(on_home, filters.regex(r"^h:(menu|about|recent|pop|hist|list|help|req)$")))
    bot.add_handler(CallbackQueryHandler(admin_mod.make_callback(app), filters.regex(r"^a:")))
    bot.add_handler(CallbackQueryHandler(on_callback, filters.regex(r"^(t|l|q|s|n|e|el|eq|sp|cp|pl|pq|p|r|w|rq):")))

    # live indexing: the bot must be a member/admin of the source chat
    live_filter = (filters.channel | filters.group) & (filters.video | filters.document | filters.audio)
    bot.add_handler(MessageHandler(app.indexer.on_message, live_filter))
    if user_client is not None:
        user_client.add_handler(MessageHandler(app.indexer.on_message, live_filter))
        from pyrogram.handlers import DeletedMessagesHandler
        user_client.add_handler(DeletedMessagesHandler(app.indexer.on_deleted))
