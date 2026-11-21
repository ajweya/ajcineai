"""Public, read-only website API and the video stream endpoint.

Nothing here exposes Telegram chat/message ids, file ids or raw filenames of sources. The browser works with
catalog ids (IMDb-style / local ids) and opaque tokens that are created server-side:
  * a stream token (expiring) is bound to one indexed file and only grants playback of that file;
  * a download token is claimed by the first Telegram account that opens its deep link.
The whole site and streaming can be switched off from the dashboard at any time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import brand
from .. import db as dbm
from ..app import App
from ..catalog import human_size, quality_sort_key
from ..delivery import DeliveryError
from ..net import client_ip
from ..parsing.languages import lang_name
from ..site import site_config
from ..requests import RequestError
from ..tmdb import poster_src
from ..streaming import (GuardedStreamingResponse, MediaResolver, RangeError, SourceGone, StreamBusy, StreamLimiter,
                         browser_hint, content_disposition, guess_mime, iter_range, parse_range)

log = logging.getLogger("ajcine.public")
ID_PAT = r"^(?:tt\d{7,10}|loc_[0-9a-f]{10})$"
PER_PAGE = 24
HINT_ORDER = {"good": 0, "maybe": 1, "poor": 2}


class RequestBody(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    year: int | None = Field(None, ge=1888, le=2100)
    kind: str | None = Field(None, pattern="^(movie|series)$")


class OptionBody(BaseModel):
    title_id: str = Field(pattern=ID_PAT)
    language: str | None = Field(None, max_length=8)
    quality: str | None = Field(None, max_length=8)      # "xx" = unknown quality
    season: int | None = Field(None, ge=0, le=99)
    episode: int | None = Field(None, ge=0, le=9999)
    pack: str | None = Field(None, pattern="^(season|series)$")


def _ts(d: dict) -> datetime:
    v = d.get("last_indexed_at")
    return v.replace(tzinfo=None) if v else datetime.min


def _card(d: dict) -> dict:
    tid = d["_id"]
    return {
        "id": tid, "title": d.get("title"), "year": d.get("year"), "kind": d.get("kind"),
        "rating": d.get("rating", d.get("imdb_rating")) if str(tid).startswith("tt") else None,
        "genres": (d.get("genres") or [])[:4], "overview": d.get("overview"),
        "poster": poster_src(d.get("poster")), "backdrop": d.get("backdrop"),
        "languages": [{"code": c, "name": lang_name(c)} for c in d.get("languages") or []],
        "qualities": d.get("qualities") or [],
    }


def build_public_router(app: App) -> APIRouter:
    s = app.settings
    r = APIRouter()
    app.streams = {
        "limiter": StreamLimiter(s.stream_max_concurrent, s.stream_max_per_ip),
        "resolver": MediaResolver(lambda: [c for c in (app.tg_user, app.tg) if c is not None]),
    }
    limiter: StreamLimiter = app.streams["limiter"]
    resolver: MediaResolver = app.streams["resolver"]
    files = lambda: app.db[dbm.FILES]  # noqa: E731

    async def guard(request: Request) -> dict:
        cfg = await site_config(app)
        if not cfg["public"]:
            raise HTTPException(404, "The website is switched off.")
        ip = client_ip(request, s.trust_proxy)
        if await app.cache.incr(f"pub:{ip}", 60) > s.public_rate_limit_per_minute:
            raise HTTPException(429, "Too many requests. Please wait a moment.")
        return cfg

    def J(data: Any, status: int = 200, cache: str = "no-store") -> JSONResponse:
        return JSONResponse(data, status_code=status, headers={"Cache-Control": cache})

    async def live_title(title_id: str) -> dict:
        t = await app.catalog.get_title(title_id)
        if not t or (t.get("file_count") or 0) <= 0:
            raise HTTPException(404, "Title not found.")
        return t

    # ---------------------------------------------------------------- site info
    @r.get("/api/public/config")
    async def config(request: Request):
        cfg = await guard(request)
        return J({"name": brand.PRODUCT, "company": brand.COMPANY, "developer": brand.DEVELOPER,
                  "streaming": cfg["streaming"], "bot": app.bot_username or None})

    # ---------------------------------------------------------------- ads
    @r.get("/api/public/ads")
    async def site_ad(request: Request):
        await guard(request)
        ad = await app.ads.pick("site_banner")
        if not ad:
            return J({"ad": None})
        await app.ads.impression(ad["_id"])
        return J({"ad": {"id": ad["_id"], "title": ad["title"], "text": ad.get("text"), "image": ad.get("image_url"),
                         "button": ad["button_text"], "href": f"/go/ad/{ad['_id']}"}})

    @r.get("/go/ad/{ad_id}")
    async def go_ad(ad_id: str, request: Request):
        ip = client_ip(request, s.trust_proxy)
        if await app.cache.incr(f"adgo:{ip}", 60) > 60:
            raise HTTPException(429, "Too many requests.")
        dest = await app.ads.click(ad_id)
        if not dest:
            raise HTTPException(404, "This link is no longer active.")
        return RedirectResponse(dest, status_code=302, headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

    # ---------------------------------------------------------------- discovery
    @r.get("/api/public/home")
    async def home(request: Request):
        await guard(request)
        key = f"pubhome:{await app.catalog.version()}"
        data = await app.cache.get(key)
        if data is None:
            movies = await app.search.recent(18, "movie")
            series = await app.search.recent(18, "series")
            popular = await app.search.popular(18)
            trending = await app.search.popular(18, days=7)
            recent = await app.search.recent(18)
            # languages that really exist in the catalog
            langs: dict[str, int] = {}
            for coll in (dbm.MOVIES, dbm.SERIES):
                async for d in app.db[coll].find({"file_count": {"$gt": 0}}, {"languages": 1}):
                    for c in d.get("languages") or []:
                        langs[c] = langs.get(c, 0) + 1
            rows = {}
            for lc in sorted(langs, key=langs.get, reverse=True)[:3]:
                found = []
                for coll in (dbm.MOVIES, dbm.SERIES):
                    found += [d async for d in app.db[coll].find(
                        {"file_count": {"$gt": 0}, "languages": lc}, {"search_tokens": 0}
                    ).sort("last_indexed_at", -1).limit(12)]
                found.sort(key=_ts, reverse=True)
                rows[lc] = {"name": lang_name(lc), "items": [_card(d) for d in found[:12]]}

            def conv(items):
                out = []
                for it in items:
                    out.append({**it, "poster": poster_src(it.get("poster")),
                                "languages": [{"code": c, "name": lang_name(c)} for c in it.get("languages", [])]})
                    out[-1].pop("score", None)
                    out[-1].pop("file_count", None)
                    out[-1].pop("verified", None)
                return out
            data = {"recent": conv(recent), "movies": conv(movies), "series": conv(series), "popular": conv(popular), "trending": conv(trending),
                    "languages": [{"code": c, "name": lang_name(c), "titles": n,
                                   } for c, n in sorted(langs.items(), key=lambda kv: -kv[1])],
                    "language_rows": [{"code": k, **v} for k, v in rows.items()]}
            await app.cache.set(key, data, ttl=120)
        return J(data, cache="public, max-age=30")

    @r.get("/api/public/search")
    async def search(request: Request, q: str = Query("", max_length=120), kind: str | None = Query(None, pattern="^(movie|series)$"),
                     page: int = Query(1, ge=1, le=50)):
        await guard(request)
        res = await app.search.search(q, kind=kind, page=page, record=True, use_ai=True)
        items = [{"id": i["id"], "title": i["title"], "year": i["year"], "kind": i["kind"], "rating": i["rating"],
                  "genres": i["genres"][:4], "overview": i["overview"],
                  "poster": poster_src(i.get("poster")),
                  "languages": [{"code": c, "name": lang_name(c)} for c in i["languages"]],
                  "qualities": i["qualities"]} for i in res["items"]]
        return J({"query": res["query"], "items": items, "total": res["total"], "page": res["page"],
                  "pages": res["pages"], "interpreted": res.get("interpreted"), "suggestions": res["suggestions"]})

    @r.get("/api/public/browse")
    async def browse(request: Request, kind: str | None = Query(None, pattern="^(movie|series)$"),
                     language: str | None = Query(None, max_length=8, pattern=r"^[a-z]{2,3}$"),
                     genre: str | None = Query(None, max_length=30), year: int | None = Query(None, ge=1888, le=2100),
                     sort: str = Query("new", pattern="^(new|rating|title)$"),
                     page: int = Query(1, ge=1, le=200)):
        await guard(request)
        colls = [dbm.MOVIES, dbm.SERIES] if kind is None else [dbm.MOVIES if kind == "movie" else dbm.SERIES]
        flt: dict = {"file_count": {"$gt": 0}}
        if language:
            flt["languages"] = language
        if genre:
            flt["genres"] = genre
        if year:
            flt["year"] = year
        docs: list[dict] = []
        total = 0
        for c in colls:
            total += await app.db[c].count_documents(flt)
            docs += [d async for d in app.db[c].find(flt, {"search_tokens": 0}).limit(1000)]
        if sort == "rating":
            docs.sort(key=lambda d: (d.get("rating") or d.get("imdb_rating") or 0, d.get("votes") or 0), reverse=True)
        elif sort == "title":
            docs.sort(key=lambda d: (d.get("title") or "").lower())
        else:
            docs.sort(key=_ts, reverse=True)
        window = docs[(page - 1) * PER_PAGE: page * PER_PAGE]
        genres: set[str] = set()
        years: set[int] = set()
        for c in colls:
            genres.update(await app.db[c].distinct("genres", {"file_count": {"$gt": 0}}))
            years.update(y for y in await app.db[c].distinct("year", {"file_count": {"$gt": 0}}) if isinstance(y, int))
        return J({"items": [_card(d) for d in window], "total": total, "page": page,
                  "pages": max(1, -(-total // PER_PAGE)), "genres": sorted(g for g in genres if g)[:40],
                  "years": sorted(years, reverse=True)[:60]})

    @r.post("/api/public/requests")
    async def request_title(request: Request, body: RequestBody):
        await guard(request)
        try:
            res = await app.requests.submit(body.title, body.year, body.kind, client_ip(request, s.trust_proxy),
                                            secret=s.web_secret_key)
        except RequestError as exc:
            raise HTTPException(429 if exc.code == "rate" else 400, exc.message)
        return J(res)

    # ---------------------------------------------------------------- title page
    @r.get("/api/public/title/{title_id}")
    async def title(request: Request, title_id: str):
        cfg = await guard(request)
        if not re.fullmatch(ID_PAT, title_id):
            raise HTTPException(404, "Title not found.")
        t = await live_title(title_id)
        out = _card(t)
        out.update({"original_title": t.get("original_title"), "runtime": t.get("runtime"),
                    "file_count": t.get("file_count"), "verified": title_id.startswith("tt"),
                    "streaming": cfg["streaming"]})
        if t.get("kind") == "series":
            seasons = await app.catalog.seasons_of(title_id)
            out["seasons"] = seasons
            out["complete_pack"] = await app.catalog.has_complete_pack(title_id)
        return J(out)

    @r.get("/api/public/title/{title_id}/episodes")
    async def episodes(request: Request, title_id: str, season: int = Query(ge=0, le=99)):
        await guard(request)
        if not re.fullmatch(ID_PAT, title_id):
            raise HTTPException(404, "Title not found.")
        await live_title(title_id)
        return J({"season": season, "has_pack": await app.catalog.has_season_pack(title_id, season),
                  "episodes": await app.catalog.episodes_of(title_id, season)})

    async def _scope(body: OptionBody) -> dict:
        """Common selection arguments for catalog queries (season/episode/pack)."""
        return {"season": body.season, "episode": body.episode, "pack": body.pack}

    @r.post("/api/public/options")
    async def options(request: Request, body: OptionBody):
        """Telegram-style button flow: language -> quality -> actions. Only choices that really exist are returned."""
        cfg = await guard(request)
        await live_title(body.title_id)
        scope = await _scope(body)
        langs = await app.catalog.languages_for(body.title_id, scope["season"], scope["episode"], scope["pack"])
        out: dict[str, Any] = {"languages": langs, "qualities": [], "files": 0,
                               "can_stream": cfg["streaming"] and body.pack is None}
        if body.language:
            quals = await app.catalog.qualities_for(body.title_id, body.language, scope["season"],
                                                    scope["episode"], scope["pack"])
            out["qualities"] = [{"quality": q["quality"], "raw": q["raw"] or "xx", "size": q["size"],
                                 "size_text": human_size(q["size"]), "files": q["files"]} for q in quals]
        return J(out)

    async def pick_files(body: OptionBody) -> list[dict]:
        q = body.quality
        found = await app.catalog.files_for(body.title_id, body.language, None if q == "xx" else q,
                                            body.season, body.episode, body.pack)
        if q == "xx":
            found = [f for f in found if not f.get("quality")]
        return found

    @r.post("/api/public/download-link")
    async def download_link(request: Request, body: OptionBody):
        await guard(request)
        if not app.bot_username:
            raise HTTPException(503, "The Telegram bot is not connected yet.")
        found = await pick_files(body)
        if not found:
            raise HTTPException(404, "That version is not available any more.")
        try:
            token = await app.delivery.create_web_token([f["_id"] for f in found], body.title_id,
                                                        client_ip(request, s.trust_proxy))
        except DeliveryError as exc:
            raise HTTPException(429 if exc.code == "rate" else 400, exc.message)
        return J({"url": f"https://t.me/{app.bot_username}?start=dl_{token}", "files": len(found),
                  "size_text": human_size(sum(f.get("size") or 0 for f in found)),
                  "expires_minutes": s.token_ttl_seconds // 60})

    @r.post("/api/public/stream-link")
    async def stream_link(request: Request, body: OptionBody):
        cfg = await guard(request)
        if not cfg["streaming"]:
            raise HTTPException(404, "Streaming is switched off.")
        if body.pack:
            raise HTTPException(400, "Packs can be downloaded in Telegram, not streamed.")
        found = await pick_files(body)
        if not found:
            raise HTTPException(404, "That version is not available any more.")
        found.sort(key=lambda f: (HINT_ORDER[browser_hint(f)], -(f.get("size") or 0)))
        f = found[0]
        token = await app.delivery.create_stream_token(f["_id"], body.title_id)
        return J({"watch": f"/watch/{token}", "hint": browser_hint(f), "size_text": human_size(f.get("size"))})

    @r.get("/api/public/watch/{token}")
    async def watch_info(request: Request, token: str):
        cfg = await guard(request)
        if not cfg["streaming"]:
            raise HTTPException(404, "Streaming is switched off.")
        try:
            doc = await app.delivery.resolve_stream(token)
        except DeliveryError as exc:
            raise HTTPException(410 if exc.code == "expired" else 404, exc.message)
        f = await files().find_one({"_id": doc["file_id"], "deleted": {"$ne": True}, "missing": {"$ne": True}})
        if not f:
            raise HTTPException(404, "This file is no longer available.")
        t = await app.catalog.get_title(f.get("title_id")) if f.get("title_id") else None
        ep = None
        if f.get("season") is not None and f.get("episode") is not None:
            ep = {"season": f["season"], "episode": f["episode"], "title": f.get("episode_title")}
        lang = (f.get("languages") or [None])[0]
        sel = {"title_id": f.get("title_id"), "language": lang, "quality": f.get("quality") or "xx",
               "season": f.get("season"), "episode": f.get("episode")}
        qualities, nxt, prv = [], None, None
        if f.get("title_id") and lang:                       # other qualities of exactly this language/episode
            for q in await app.catalog.qualities_for(f["title_id"], lang, f.get("season"), f.get("episode")):
                raw = q["raw"] or "xx"
                qualities.append({"quality": q["quality"], "size_text": human_size(q["size"]), "current": raw == sel["quality"],
                                  "select": {**sel, "quality": raw}})
        if ep and f.get("title_id"):                         # the next episode (same season, else the next season)
            tid, cur_s, cur_e = f["title_id"], f["season"], f["episode"]
            later = [e for e in await app.catalog.episodes_of(tid, cur_s) if e["episode"] > cur_e]
            if later:
                nxt = {"season": cur_s, "episode": later[0]["episode"], "title": later[0].get("title")}
            else:
                for srow in await app.catalog.seasons_of(tid):
                    if srow["season"] > cur_s:
                        eps = await app.catalog.episodes_of(tid, srow["season"])
                        if eps:
                            nxt = {"season": srow["season"], "episode": eps[0]["episode"], "title": eps[0].get("title")}
                            break
            if nxt:
                nxt["select"] = {"title_id": tid, "language": lang, "quality": None, "season": nxt["season"], "episode": nxt["episode"]}
            earlier = [e for e in await app.catalog.episodes_of(tid, cur_s) if e["episode"] < cur_e]
            if earlier:
                prv = {"season": cur_s, "episode": earlier[-1]["episode"], "title": earlier[-1].get("title")}
            else:
                for srow in sorted(await app.catalog.seasons_of(tid), key=lambda r: -r["season"]):
                    if srow["season"] < cur_s:
                        eps = await app.catalog.episodes_of(tid, srow["season"])
                        if eps:
                            prv = {"season": srow["season"], "episode": eps[-1]["episode"], "title": eps[-1].get("title")}
                            break
            if prv:
                prv["select"] = {"title_id": tid, "language": lang, "quality": None, "season": prv["season"], "episode": prv["episode"]}
        return J({"qualities": qualities if len(qualities) > 1 else [], "next": nxt, "prev": prv,
                  "title": (t or {}).get("title"), "year": (t or {}).get("year"), "title_id": f.get("title_id"),
                  "kind": (t or {}).get("kind"), "episode": ep, "quality": f.get("quality"),
                  "language": lang_name(f["languages"][0]) if f.get("languages") else None,
                  "size_text": human_size(f.get("size")), "hint": browser_hint(f),
                  "container": (re.search(r"\.([A-Za-z0-9]{2,4})$", f.get("file_name") or "") or [None, ""])[1].lower(),
                  "poster": poster_src(t.get("poster")) if t else None, "backdrop": (t or {}).get("backdrop"),
                  "stream": f"/stream/{token}", "bot": app.bot_username or None,
                  "select": sel})

    # ---------------------------------------------------------------- the stream itself
    @r.api_route("/stream/{token}", methods=["GET", "HEAD"])
    async def stream(request: Request, token: str):
        cfg = await site_config(app)
        if not cfg["streaming"]:
            raise HTTPException(404, "Streaming is switched off.")
        ip = client_ip(request, s.trust_proxy)
        try:
            doc = await app.delivery.resolve_stream(token)
        except DeliveryError as exc:
            raise HTTPException(410 if exc.code == "expired" else 404, exc.message)
        f = await files().find_one({"_id": doc["file_id"], "deleted": {"$ne": True}, "missing": {"$ne": True}})
        if not f:
            raise HTTPException(404, "This file is no longer available.")
        try:
            res = await resolver.resolve(f)
        except SourceGone:
            resolver.forget(f)
            await app.delivery.mark_missing(f)
            raise HTTPException(404, "This file is no longer available.")
        except StreamBusy:
            raise HTTPException(503, "Busy, try again shortly.", headers={"Retry-After": "5"})
        size = res.size
        try:
            rng = parse_range(request.headers.get("range"), size)
        except RangeError:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start, end = rng if rng else (0, size - 1)
        headers = {
            "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
            "Content-Disposition": content_disposition(f.get("file_name")),
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        }
        if rng:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206 if rng else 200
        mime = guess_mime(f, res.mime)
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers, media_type=mime)
        try:
            limiter.acquire(ip)
        except StreamBusy:
            raise HTTPException(429, "Too many streams from your connection. Close another player and retry.",
                                headers={"Retry-After": "10"})
        gen = iter_range(res.client, res.message, start, end)

        async def cleanup() -> None:
            try:
                await gen.aclose()
            finally:
                limiter.release(ip)

        # Read the first chunk before answering so Telegram failures become a proper HTTP error.
        try:
            first = await gen.__anext__()
        except StopAsyncIteration:
            first = b""
        except Exception as exc:
            await cleanup()
            resolver.forget(f)
            log.warning("stream start failed for %s: %s", f["_id"], type(exc).__name__)
            raise HTTPException(502, "Telegram did not return the file. Try again in a moment.")

        async def body():
            if first:
                yield first
            async for chunk in gen:
                yield chunk

        if start == 0:
            try:
                await app.delivery._log(0, f, "streamed")
            except Exception:
                pass
        return GuardedStreamingResponse(body(), cleanup, status_code=status, headers=headers, media_type=mime)

    return r
