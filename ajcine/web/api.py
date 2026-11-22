"""FastAPI backend: health check, authenticated admin API, dashboard hosting.

Security model
  * passwords: scrypt; sessions: random token, only its SHA-256 is stored;
  * cookie: HttpOnly + SameSite=Strict (+ Secure when WEB_SECURE_COOKIES);
  * CSRF: every state-changing request must carry `X-Requested-With: ajcine`
    (a custom header cannot be sent cross-site without CORS approval, and no
    CORS is enabled);
  * brute-force: per-IP and per-username login throttling;
  * RBAC: every route declares the permission it needs (see users.PERMISSIONS);
  * secrets are never returned by any endpoint.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import brand
from .. import db as dbm
from ..app import App
from ..corrections import ReviewError, valid_title_id
from ..logging_setup import recent_logs
from ..requests import RequestError
from ..tmdb import valid_image_url
from ..net import client_ip as _client_ip
from ..site import set_site_config, site_config
from ..tools.backup import create_backup, list_backups
from ..users import ROLE_RANK, hash_password, role_allows, verify_password

log = logging.getLogger("ajcine.web")
STATIC = Path(__file__).parent / "static"
SITE = Path(__file__).parent / "site"
CDN = "https://cdn.jsdelivr.net"
PUBLIC_PREFIXES = ("/assets", "/api/public", "/stream", "/go/", "/title/", "/watch/")
PUBLIC_PAGES = {"/", "/search", "/browse", "/movies", "/series", "/anime", "/library", "/settings"}


def build_csp(image_base: str = "", any_https_images: bool = False, public: bool = False) -> str:
    """`any_https_images` is needed for ad images, which an administrator can host anywhere over https.
    `public` is the visitor-facing site: it may load pinned, integrity-checked libraries from jsDelivr and fonts from
    Google Fonts. The admin dashboard never gets those allowances."""
    from urllib.parse import urlparse
    host = urlparse(image_base).netloc if image_base else ""
    img = "'self' data:" + (" https:" if any_https_images else (f" https://{host}" if host else ""))
    if public:
        style, script = f"'self' {CDN} https://fonts.googleapis.com", f"'self' {CDN}"
        font = f"font-src 'self' data: {CDN} https://fonts.gstatic.com; connect-src 'self' {CDN}; "
    else:
        style, script, font = "'self'", "'self'", ""
    return (f"default-src 'self'; img-src {img}; style-src {style}; script-src {script}; media-src 'self'; {font}"
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


_OLD_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; media-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SecurityHeaders:
    """Pure ASGI middleware (BaseHTTPMiddleware interferes with streamed bodies and client disconnects)."""

    def __init__(self, app, csp: str, public_csp: str | None = None):
        self.app = app
        self.csp = csp
        self.public_csp = public_csp or csp

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        csp = self.public_csp if (path in PUBLIC_PAGES or path.startswith(PUBLIC_PREFIXES)) else self.csp

        async def wrapped(message):
            if message["type"] == "http.response.start":
                from starlette.datastructures import MutableHeaders
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "no-referrer")
                h.setdefault("Content-Security-Policy", csp)
                if path.startswith("/api/") and "cache-control" not in h:
                    h["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, wrapped)
COOKIE = "ajcine_session"
ID_PAT = r"^(?:tt\d{7,10}|loc_[0-9a-f]{10})$"
MAX_POSTER = 5 * 1024 * 1024


def now() -> datetime:
    return datetime.now(timezone.utc)


def clean(v: Any) -> Any:
    """Make Mongo documents JSON-safe."""
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items() if k not in ("password_hash", "search_tokens")}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, ObjectId):
        return str(v)
    return v


def J(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(clean(data), status_code=status)


# ---- request bodies --------------------------------------------------------------------------
class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ApproveBody(BaseModel):
    imdb_id: str = Field(pattern=r"^tt\d{7,10}$")
    scope: str = Field("group", pattern="^(group|file)$")
    overrides: dict | None = None


class LocalBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    year: int | None = Field(None, ge=1888, le=2100)
    kind: str = Field(pattern="^(movie|series)$")
    scope: str = Field("group", pattern="^(group|file)$")


class ScopeBody(BaseModel):
    scope: str = Field("file", pattern="^(group|file)$")


class FileEditBody(BaseModel):
    season: int | None = Field(None, ge=0, le=99)
    episode: int | None = Field(None, ge=0, le=9999)
    episode_title: str | None = Field(None, max_length=200)
    quality: str | None = Field(None, max_length=8)
    languages: list[str] | None = Field(None, max_length=8)
    content_type: str | None = Field(None, pattern="^(movie|series)$")


class TitleEditBody(BaseModel):
    title: str | None = Field(None, max_length=200)
    year: int | None = Field(None, ge=1888, le=2100)
    original_title: str | None = Field(None, max_length=200)
    genres: list[str] | None = Field(None, max_length=8)
    overview: str | None = Field(None, max_length=2000)
    kind: str | None = Field(None, pattern="^(movie|series)$")


class MergeBody(BaseModel):
    source: str = Field(pattern=ID_PAT)
    target: str = Field(pattern=ID_PAT)


class ImdbIdBody(BaseModel):
    imdb_id: str = Field(pattern=r"^tt\d{7,10}$")


class AIConfigBody(BaseModel):
    enabled: bool | None = None
    model: str | None = Field(None, max_length=120)
    fallbacks: list[str] | None = Field(None, max_length=6)


class SourceBody(BaseModel):
    chat: str = Field(min_length=2, max_length=120)


class SourcePatch(BaseModel):
    enabled: bool | None = None
    live: bool | None = None


class JobBody(BaseModel):
    chat_id: int
    kind: str = Field("history", pattern="^(history|reindex)$")
    start_id: int = Field(1, ge=1)


class SiteBody(BaseModel):
    public: bool | None = None
    streaming: bool | None = None


class PosterBody(BaseModel):
    url: str = Field(min_length=10, max_length=500)


class RequestStatusBody(BaseModel):
    status: str = Field(pattern="^(open|done|declined)$")


class AdBody(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    text: str = Field(default="", max_length=300)
    link_url: str = Field(max_length=500)
    image_url: str | None = Field(default=None, max_length=500)
    button_text: str = Field(default="Learn more", max_length=24)
    placements: list[str] = Field(min_length=1, max_length=3)
    weight: int = Field(default=1, ge=1, le=10)
    active: bool = True
    starts_at: str | None = None
    ends_at: str | None = None


class AdSwitchBody(BaseModel):
    enabled: bool


class AdActiveBody(BaseModel):
    active: bool


class BroadcastBody(BaseModel):
    text: str = Field(min_length=1, max_length=3900)
    image_url: str | None = Field(default=None, max_length=500)
    button_text: str | None = Field(default=None, max_length=24)
    button_url: str | None = Field(default=None, max_length=500)
    audience: str = Field(default="users", pattern="^(users|groups|all)$")


class BroadcastTestBody(BaseModel):
    telegram_id: int = Field(ge=1)


class PermBody(BaseModel):
    permission: str = Field(pattern="^(search|download)$")
    allowed: bool


class BanBody(BaseModel):
    reason: str | None = Field(None, max_length=200)


class AdminBody(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-]{3,32}$")
    password: str = Field(min_length=10, max_length=256)
    role: str = Field(pattern="^(viewer|moderator|admin)$")


class TgAdminBody(BaseModel):
    telegram_id: int
    role: str = Field(pattern="^(viewer|moderator|admin)$")


def create_web_app(app: App) -> FastAPI:
    s = app.settings
    api = FastAPI(title=f"{brand.PRODUCT} API", version=brand.VERSION, docs_url=None, redoc_url=None, openapi_url=None)

    # ---------------------------------------------------------------- auth helpers
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def current(request: Request) -> dict | None:
        tok = request.cookies.get(COOKIE)
        if not tok:
            return None
        sess = await app.db[dbm.WEB_SESSIONS].find_one({"_id": _hash(tok)})
        if not sess:
            return None
        exp = sess["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now():
            return None
        acct = await app.db[dbm.ADMINS].find_one({"username": sess["username"], "active": {"$ne": False}})
        if not acct:
            return None
        return {"username": acct["username"], "role": acct["role"]}

    def require(permission: str):
        async def dep(request: Request) -> dict:
            user = await current(request)
            if not user:
                raise HTTPException(401, "authentication required")
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "ajcine":
                raise HTTPException(403, "missing CSRF header")
            if not role_allows(user["role"], permission):
                raise HTTPException(403, "insufficient permissions")
            request.state.user = user
            return user
        return dep

    def actor(user: dict) -> dict:
        return {"type": "web", "username": user["username"]}

    def client_ip(request: Request) -> str:
        return _client_ip(request, s.trust_proxy)

    @api.on_event("startup")
    async def bootstrap_admin() -> None:
        if await app.db[dbm.ADMINS].count_documents({"username": {"$ne": None}}) == 0:
            if s.admin_bootstrap_password and len(s.admin_bootstrap_password) >= 10:
                await app.db[dbm.ADMINS].insert_one({
                    "username": s.admin_bootstrap_user, "password_hash": hash_password(s.admin_bootstrap_password),
                    "role": "owner", "active": True, "created_at": now()})
                log.info("bootstrap dashboard owner created: %s", s.admin_bootstrap_user)
            else:
                log.warning("no dashboard admin exists; set ADMIN_BOOTSTRAP_PASSWORD (min 10 chars) and restart")

    @api.exception_handler(ReviewError)
    async def review_error(_: Request, exc: ReviewError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @api.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @api.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.error("unhandled API error: %s: %s", type(exc).__name__, str(exc)[:200])
        return JSONResponse({"detail": "internal error"}, status_code=500)

    _img = s.tmdb_image_base if s.tmdb_enabled else ""
    api.add_middleware(SecurityHeaders, csp=build_csp(_img, s.ads_enabled), public_csp=build_csp(_img, s.ads_enabled, public=True))

    # ---------------------------------------------------------------- health
    @api.get("/healthz")
    async def healthz():
        checks = {}
        try:
            for coll in (dbm.SETTINGS, dbm.MOVIES, dbm.FILES, dbm.USERS, dbm.SEARCH_HISTORY):
                await app.db[coll].find_one({}, {"_id": 1})
            checks["mongo"] = True
        except Exception:
            checks["mongo"] = False
        checks["cache"] = await app.cache.ping()
        checks["telegram"] = app.tg is not None and bool(getattr(app.tg, "is_connected", True))
        ok = checks["mongo"]  # TMDB/AI/cache degrade gracefully; Mongo is mandatory
        return JSONResponse({"ok": ok, "checks": checks, "service": brand.PRODUCT, "version": brand.VERSION},
                            status_code=200 if ok else 503)

    # ---------------------------------------------------------------- session
    @api.post("/api/login")
    async def login(body: LoginBody, request: Request, response: Response):
        ip = client_ip(request)
        uname = body.username.lower()
        if await app.cache.incr(f"login_ip:{ip}", 900) > 20 or await app.cache.incr(f"login_u:{uname}", 900) > 10:
            raise HTTPException(429, "too many attempts, try again later")
        acct = await app.db[dbm.ADMINS].find_one({"username": body.username, "active": {"$ne": False}})
        ok = bool(acct and acct.get("password_hash") and verify_password(body.password, acct["password_hash"]))
        if not ok:
            if not acct:  # equalize timing a little
                verify_password(body.password, "scrypt$16384$8$1$00$00")
            log.warning("failed dashboard login for %s from %s", body.username[:32], ip)
            raise HTTPException(401, "invalid credentials")
        token = secrets.token_urlsafe(32)
        await app.db[dbm.WEB_SESSIONS].insert_one({
            "_id": _hash(token), "username": acct["username"], "created_at": now(), "ip": ip,
            "expires_at": now() + timedelta(hours=s.web_session_hours)})
        response.set_cookie(COOKIE, token, max_age=s.web_session_hours * 3600, httponly=True, samesite="strict",
                            secure=s.web_secure_cookies, path="/")
        await app.db[dbm.ADMINS].update_one({"_id": acct["_id"]}, {"$set": {"last_login": now()}})
        return {"username": acct["username"], "role": acct["role"]}

    @api.post("/api/logout")
    async def logout(request: Request, response: Response):
        tok = request.cookies.get(COOKIE)
        if tok:
            await app.db[dbm.WEB_SESSIONS].delete_one({"_id": _hash(tok)})
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/api/me")
    async def me(request: Request):
        user = await current(request)
        if not user:
            raise HTTPException(401, "authentication required")
        return {
    **user,
    "product": brand.PRODUCT,
    "company": "AJ APPLICATIONS",
    "developer": "Ahammed Ajmal S",
    "version": brand.VERSION
}

    # ---------------------------------------------------------------- overview
    @api.get("/api/stats")
    async def stats(user=Depends(require("view"))):
        st = await app.catalog.stats()
        st["ai"] = await app.openrouter.status()
        st["tmdb"] = {"state": "ready" if app.lookup.enabled else "not configured"}
        st["cache_backend"] = app.cache.backend
        return J(st)

    # ---------------------------------------------------------------- media
    @api.get("/api/media")
    async def media_list(kind: str | None = None, q: str | None = None, page: int = 1, per_page: int = 25,
                         user=Depends(require("view"))):
        per_page = max(1, min(per_page, 100))
        colls = [dbm.MOVIES, dbm.SERIES] if kind not in ("movie", "series") else [dbm.MOVIES if kind == "movie" else dbm.SERIES]
        f: dict = {}
        if q:
            f["title"] = {"$regex": re.escape(q[:60]), "$options": "i"}
        items, total = [], 0
        for c in colls:
            total += await app.db[c].count_documents(f)
        for c in colls:
            cur = app.db[c].find(f, {"search_tokens": 0}).sort("updated_at", -1).limit(per_page * page)
            items += [d async for d in cur]
        items.sort(key=lambda d: d.get("updated_at") or now(), reverse=True)
        window = items[(page - 1) * per_page: page * per_page]
        return J({"total": total, "page": page, "per_page": per_page, "items": window})

    @api.get("/api/media/{title_id}")
    async def media_detail(title_id: str, user=Depends(require("view"))):
        if not valid_title_id(title_id):
            raise HTTPException(400, "bad id")
        doc = await app.catalog.get_title(title_id)
        if not doc:
            raise HTTPException(404, "not found")
        files = [f async for f in app.db[dbm.FILES].find({"title_id": title_id, "deleted": {"$ne": True}}).sort(
            [("season", 1), ("episode", 1), ("quality", 1)]).limit(500)]
        out = {"title": doc, "files": files}
        if doc["kind"] == "series":
            out["seasons"] = await app.catalog.seasons_of(title_id)
            out["missing"] = await app.catalog.missing_episodes(title_id)
        return J(out)

    @api.patch("/api/media/{title_id}")
    async def media_edit(title_id: str, body: TitleEditBody, user=Depends(require("media_edit"))):
        if not valid_title_id(title_id):
            raise HTTPException(400, "bad id")
        fields = body.model_dump(exclude_unset=True)
        return J(await app.review.edit_title(title_id, fields, actor(user)))

    @api.post("/api/media/{title_id}/imdb")
    async def media_imdb(title_id: str, body: ImdbIdBody, user=Depends(require("media_edit"))):
        return J(await app.review.change_imdb_id(title_id, body.imdb_id, actor(user)))

    @api.post("/api/media/merge")
    async def media_merge(body: MergeBody, user=Depends(require("media_edit"))):
        return J(await app.review.merge(body.source, body.target, actor(user)))

    @api.put("/api/media/{title_id}/poster")
    async def media_poster(title_id: str, body: PosterBody, user=Depends(require("media_edit"))):
        """Posters are stored as image URLs; no image is uploaded or saved on the server."""
        if not valid_title_id(title_id) or not await app.catalog.get_title(title_id):
            raise HTTPException(404, "not found")
        url = body.url.strip()
        if not valid_image_url(url):
            raise HTTPException(400, "poster must be a direct https:// image URL")
        await app.catalog.set_poster(title_id, url, "manual")
        await app.db[dbm.CORRECTIONS].insert_one({"type": "poster", "actor": actor(user), "imdb_id": title_id,
                                                  "poster": url, "active": True, "created_at": now()})
        return {"poster": url}

    @api.post("/api/media/{title_id}/poster/refresh")
    async def media_poster_refresh(title_id: str, user=Depends(require("media_edit"))):
        if not valid_title_id(title_id):
            raise HTTPException(404, "not found")
        res = await app.posters.refresh_title(title_id, force=True)
        if res == "missing":
            raise HTTPException(404, "not found")
        if res == "disabled":
            raise HTTPException(400, "TMDB is not configured (set TMDB_API_KEY or TMDB_READ_TOKEN)")
        t = await app.catalog.get_title(title_id)
        return J({"result": res, "poster": t.get("poster")})

    @api.delete("/api/media/{title_id}/poster")
    async def media_poster_delete(title_id: str, user=Depends(require("media_edit"))):
        if not valid_title_id(title_id) or not await app.catalog.get_title(title_id):
            raise HTTPException(404, "not found")
        await app.catalog.set_poster(title_id, None)
        return {"ok": True}

    @api.get("/posters/{name}")
    async def poster(name: str):
        if not re.fullmatch(r"(?:tt\d{7,10}|loc_[0-9a-f]{10})\.(?:jpg|png)", name):
            raise HTTPException(404)
        p = Path(s.poster_dir) / name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=3600"})

    @api.patch("/api/files/{file_id}")
    async def file_edit(file_id: str, body: FileEditBody, user=Depends(require("media_edit"))):
        return J(await app.review.edit_file(file_id, body.model_dump(exclude_unset=True), actor(user)))

    @api.get("/api/duplicates")
    async def duplicates(user=Depends(require("view"))):
        return J(await app.catalog.duplicate_groups())

    # ---------------------------------------------------------------- review
    @api.get("/api/review")
    async def review_list(status: str = "pending", page: int = 1, q: str | None = None, user=Depends(require("review"))):
        return J(await app.review.queue(status, page, 20, q))

    @api.get("/api/review/{file_id}")
    async def review_item(file_id: str, user=Depends(require("review"))):
        return J(await app.review.item(file_id))

    @api.get("/api/titles/search")
    async def titles_search(q: str, year: int | None = None, kind: str | None = None, user=Depends(require("review"))):
        if kind not in (None, "movie", "series"):
            raise HTTPException(400, "bad kind")
        return J(await app.review.search_titles(q[:120], year, kind))

    @api.post("/api/review/{file_id}/approve")
    async def review_approve(file_id: str, body: ApproveBody, user=Depends(require("review"))):
        return J(await app.review.approve(file_id, body.imdb_id, actor(user), body.scope, body.overrides))

    @api.post("/api/review/{file_id}/local")
    async def review_local(file_id: str, body: LocalBody, user=Depends(require("review"))):
        return J(await app.review.approve_local(file_id, body.title, body.year, body.kind, actor(user), body.scope))

    @api.post("/api/review/{file_id}/reject")
    async def review_reject(file_id: str, body: ScopeBody, user=Depends(require("review"))):
        return J(await app.review.reject(file_id, actor(user), body.scope))

    @api.get("/api/corrections")
    async def corrections(limit: int = 100, title_id: str | None = None, user=Depends(require("review"))):
        return J(await app.review.history(min(limit, 300), None, title_id))

    @api.post("/api/corrections/{cid}/undo")
    async def correction_undo(cid: str, user=Depends(require("review"))):
        if not re.fullmatch(r"[0-9a-f]{24}", cid):
            raise HTTPException(400, "bad id")
        return {"ok": await app.review.undo_correction(cid, actor(user))}

    # ---------------------------------------------------------------- AI
    @api.get("/api/ai/status")
    async def ai_status(user=Depends(require("view"))):
        return J(await app.openrouter.status())

    @api.get("/api/ai/models")
    async def ai_models(user=Depends(require("ai"))):
        return J({"models": await app.openrouter.free_models(80)})

    @api.get("/api/ai/usage")
    async def ai_usage(user=Depends(require("view"))):
        return J(await app.openrouter.usage(7))

    @api.post("/api/ai/config")
    async def ai_config(body: AIConfigBody, user=Depends(require("ai"))):
        return J(await app.openrouter.set_config(body.enabled, body.model, body.fallbacks))

    @api.post("/api/ai/test")
    async def ai_test(user=Depends(require("ai"))):
        return J(await app.openrouter.test())

    @api.get("/api/ai/queue")
    async def ai_queue(user=Depends(require("view"))):
        return J({"pending": await app.db[dbm.QUEUE].count_documents({"status": "pending"}),
                  "ai_attempted": await app.db[dbm.QUEUE].count_documents({"status": "pending", "ai_attempted": True})})

    # ---------------------------------------------------------------- TMDB
    @api.get("/api/tmdb/status")
    async def tmdb_status(user=Depends(require("view"))):
        return J({"configured": app.lookup.enabled, "auth": "read token" if s.tmdb_read_token else "api key" if s.tmdb_api_key else None,
                  "cached_lookups": await app.db[dbm.TITLE_CACHE].count_documents({}),
                  "posters": True, "api_base": s.tmdb_api_base})

    @api.get("/api/tmdb/health")
    async def tmdb_health(user=Depends(require("tmdb"))):
        return J(await app.lookup.probe())

    @api.post("/api/tmdb/clear-cache")
    async def tmdb_clear_cache(user=Depends(require("tmdb"))):
        r = await app.db[dbm.TITLE_CACHE].delete_many({})
        await app.lookup.reload()
        return {"cleared": r.deleted_count}

    # ---------------------------------------------------------------- indexing
    @api.get("/api/sources")
    async def sources(user=Depends(require("view"))):
        return J([d async for d in app.db[dbm.SOURCES].find({}).sort("created_at", 1)])

    @api.post("/api/sources")
    async def source_add(body: SourceBody, user=Depends(require("sources"))):
        raw = body.chat.strip()
        m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,32})", raw)
        ref: Any = int(raw) if re.fullmatch(r"-?\d{5,15}", raw) else (m.group(1) if m else raw)
        try:
            return J(await app.indexer.add_source(ref))
        except Exception as exc:
            raise HTTPException(400, str(exc)[:300])

    @api.patch("/api/sources/{chat_id}")
    async def source_patch(chat_id: int, body: SourcePatch, user=Depends(require("sources"))):
        if not await app.indexer.set_source(chat_id, body.enabled, body.live):
            raise HTTPException(404, "not found")
        return {"ok": True}

    @api.delete("/api/sources/{chat_id}")
    async def source_delete(chat_id: int, purge: bool = False, user=Depends(require("sources"))):
        await app.indexer.remove_source(chat_id, purge)
        return {"ok": True}

    @api.post("/api/sources/{chat_id}/test")
    async def source_test(chat_id: int, user=Depends(require("sources"))):
        ok, why = await app.indexer.access_test(chat_id)
        return {"ok": ok, "detail": why}

    @api.get("/api/jobs")
    async def jobs(user=Depends(require("view"))):
        return J(await app.indexer.list_jobs(50))

    @api.post("/api/jobs")
    async def job_start(body: JobBody, user=Depends(require("indexing"))):
        if not await app.db[dbm.SOURCES].find_one({"_id": body.chat_id}):
            raise HTTPException(404, "unknown source")
        try:
            return {"id": await app.indexer.start_job(body.chat_id, body.kind, body.start_id)}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @api.post("/api/jobs/{jid}/{action}")
    async def job_action(jid: str, action: str, user=Depends(require("indexing"))):
        fn = {"pause": app.indexer.pause_job, "resume": app.indexer.resume_job, "cancel": app.indexer.cancel_job}.get(action)
        if not fn or not re.fullmatch(r"[0-9a-f]{12}", jid):
            raise HTTPException(400, "bad request")
        return {"ok": await fn(jid)}

    # ---------------------------------------------------------------- users
    @api.get("/api/users")
    async def users_list(page: int = 1, q: str | None = None, banned: bool | None = None, user=Depends(require("users"))):
        return J(await app.users.list(page, 25, q, banned))

    @api.get("/api/users/{uid}")
    async def user_detail(uid: int, user=Depends(require("users"))):
        u = await app.users.get(uid)
        if not u:
            raise HTTPException(404, "not found")
        return J({"user": {**u, "id": u.pop("_id")}, **await app.users.activity(uid, 40)})

    @api.post("/api/users/{uid}/ban")
    async def user_ban(uid: int, body: BanBody, user=Depends(require("users"))):
        if not await app.users.ban(uid, body.reason):
            raise HTTPException(400, "this user cannot be banned")
        return {"ok": True}

    @api.post("/api/users/{uid}/unban")
    async def user_unban(uid: int, user=Depends(require("users"))):
        return {"ok": await app.users.unban(uid)}

    @api.post("/api/users/{uid}/permission")
    async def user_perm(uid: int, body: PermBody, user=Depends(require("users"))):
        return {"ok": await app.users.set_permission(uid, body.permission, body.allowed)}

    # ---------------------------------------------------------------- system
    @api.get("/api/system")
    async def system(user=Depends(require("view"))):
        import time
        return J({"uptime_s": int(time.time() - app.started_at), "cache_backend": app.cache.backend,
                  "cache_ok": await app.cache.ping(), "telegram": app.tg is not None,
                  "user_session": app.tg_user is not None, "version": brand.VERSION,
                  "settings": {"match_auto_threshold": s.match_auto_threshold,
                               "match_review_threshold": s.match_review_threshold,
                               "token_ttl_seconds": s.token_ttl_seconds, "rate_limit_per_minute": s.rate_limit_per_minute,
                               "tmdb": app.lookup.enabled, "log_level": s.log_level}})

    @api.get("/api/site-config")
    async def site_cfg(user=Depends(require("view"))):
        cfg = await site_config(app)
        return J({**cfg, "bot_username": app.bot_username or None, "limits": {
            "max_concurrent": s.stream_max_concurrent, "max_per_ip": s.stream_max_per_ip,
            "token_ttl_seconds": s.stream_token_ttl_seconds},
            "active_streams": (app.streams or {}).get("limiter").total if app.streams else 0})

    @api.put("/api/site-config")
    async def site_cfg_set(body: SiteBody, user=Depends(require("settings"))):
        cfg = await set_site_config(app, body.public, body.streaming)
        log.info("site config changed by %s: %s", user["username"], cfg)
        return J(cfg)

    @api.get("/api/databases")
    async def databases(user=Depends(require("view"))):
        """Database layout and health. Hosts and names only: connection strings are never returned."""
        rows = app.db.describe() if hasattr(app.db, "describe") else []
        health = await app.db.ping() if hasattr(app.db, "ping") else {}
        for r in rows:
            r["ok"] = health.get(r["role"])
            try:
                r["documents"] = {c: await app.db[c].count_documents({}) for c in r["collections"]}
            except Exception:
                r["documents"] = {}
        return J(rows)

    @api.get("/api/requests")
    async def requests_list(status: str = "open", user=Depends(require("view"))):
        if status not in ("open", "done", "declined"):
            raise HTTPException(400, "bad status")
        return J(await app.requests.listing(status))

    @api.put("/api/requests/{rid}")
    async def request_status(rid: str, body: RequestStatusBody, user=Depends(require("review"))):
        try:
            found = await app.requests.set_status(rid, body.status)
        except RequestError as exc:
            raise HTTPException(400, exc.message)
        if not found:
            raise HTTPException(404, "not found")
        return {"ok": True}

    @api.delete("/api/requests/{rid}")
    async def request_delete(rid: str, user=Depends(require("review"))):
        return {"ok": await app.requests.delete(rid)}

    # ---------------------------------------------------------------- ads
    @api.get("/api/ads")
    async def ads_list(user=Depends(require("ads"))):
        return J({"enabled": await app.ads.enabled(), "items": await app.ads.listing()})

    @api.put("/api/ads-switch")
    async def ads_switch(body: AdSwitchBody, user=Depends(require("ads"))):
        await app.ads.set_enabled(body.enabled)
        return {"enabled": body.enabled}

    @api.post("/api/ads")
    async def ads_create(body: AdBody, user=Depends(require("ads"))):
        from ..ads import AdError
        try:
            return J(await app.ads.create(body.model_dump(), user["username"]))
        except AdError as exc:
            raise HTTPException(400, str(exc))

    @api.put("/api/ads/{ad_id}")
    async def ads_update(ad_id: str, body: AdBody, user=Depends(require("ads"))):
        from ..ads import AdError
        try:
            found = await app.ads.update(ad_id, body.model_dump())
        except AdError as exc:
            raise HTTPException(400, str(exc))
        if not found:
            raise HTTPException(404, "not found")
        return {"ok": True}

    @api.put("/api/ads/{ad_id}/active")
    async def ads_active(ad_id: str, body: AdActiveBody, user=Depends(require("ads"))):
        if not await app.ads.set_active(ad_id, body.active):
            raise HTTPException(404, "not found")
        return {"ok": True}

    @api.delete("/api/ads/{ad_id}")
    async def ads_delete(ad_id: str, user=Depends(require("ads"))):
        return {"ok": await app.ads.delete(ad_id)}

    # ---------------------------------------------------------------- broadcasts
    @api.get("/api/broadcasts")
    async def broadcasts_list(user=Depends(require("broadcast"))):
        return J({"audience": await app.chats.counts(), "items": await app.broadcasts.listing()})

    @api.get("/api/chats")
    async def chats_list(user=Depends(require("broadcast"))):
        return J(await app.chats.listing())

    # ---------------------------------------------------------------- analytics
    @api.get("/api/analytics")
    async def analytics(days: int = 30, user=Depends(require("view"))):
        """Real activity from the search history and delivery log (nothing estimated)."""
        days = max(1, min(int(days), 90))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        today = datetime.now(timezone.utc).date()
        labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        series = {"searches": dict.fromkeys(labels, 0), "deliveries": dict.fromkeys(labels, 0), "streams": dict.fromkeys(labels, 0)}
        users: set[int] = set()
        async for r in app.db[dbm.SEARCH_HISTORY].find({"ts": {"$gte": since}}, {"ts": 1, "user_id": 1}).limit(200000):
            day = r["ts"].date().isoformat()
            if day in series["searches"]:
                series["searches"][day] += 1
            if r.get("user_id") is not None:
                users.add(r["user_id"])
        async for r in app.db[dbm.DELIVERIES].find({"ts": {"$gte": since}}, {"ts": 1, "status": 1}).limit(200000):
            day = r["ts"].date().isoformat()
            key = "streams" if r.get("status") == "streamed" else "deliveries"
            if day in series[key]:
                series[key][day] += 1

        async def top_queries(match: dict) -> list[dict]:
            pipe = [{"$match": {"ts": {"$gte": since}, "norm_query": {"$nin": [None, ""]}, **match}},
                    {"$group": {"_id": "$norm_query", "n": {"$sum": 1}, "q": {"$last": "$query"}}},
                    {"$sort": {"n": -1}}, {"$limit": 15}]
            return [{"query": r["q"], "count": r["n"]} async for r in app.db[dbm.SEARCH_HISTORY].aggregate(pipe)]

        top_titles = []
        pipe = [{"$match": {"ts": {"$gte": since}, "title_id": {"$ne": None}}},
                {"$group": {"_id": "$title_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10}]
        async for r in app.db[dbm.DELIVERIES].aggregate(pipe):
            t = await app.catalog.get_title(r["_id"])
            top_titles.append({"id": r["_id"], "title": (t or {}).get("title") or r["_id"], "year": (t or {}).get("year"),
                               "count": r["n"]})
        return J({"days": days, "labels": labels, **{k: [v[d] for d in labels] for k, v in series.items()},
                  "active_users": len(users),
                  "top_searches": await top_queries({"results": {"$gt": 0}}),
                  "no_results": await top_queries({"results": 0}),
                  "top_titles": top_titles,
                  "users": {"total": await app.db[dbm.USERS].count_documents({}),
                            "blocked": await app.db[dbm.USERS].count_documents({"blocked": True}),
                            "banned": await app.db[dbm.USERS].count_documents({"banned": True})}})

    @api.post("/api/broadcasts")
    async def broadcasts_create(body: BroadcastBody, user=Depends(require("broadcast"))):
        from ..broadcast import BroadcastError
        try:
            return J(await app.broadcasts.create(body.model_dump(), user["username"]))
        except BroadcastError as exc:
            raise HTTPException(400, str(exc))

    @api.post("/api/broadcasts/{bid}/start")
    async def broadcasts_start(bid: str, user=Depends(require("broadcast"))):
        from ..broadcast import BroadcastError
        try:
            return J(await app.broadcasts.start(bid))
        except BroadcastError as exc:
            raise HTTPException(400, str(exc))

    @api.post("/api/broadcasts/{bid}/cancel")
    async def broadcasts_cancel(bid: str, user=Depends(require("broadcast"))):
        return {"ok": await app.broadcasts.cancel(bid)}

    @api.post("/api/broadcasts/{bid}/test")
    async def broadcasts_test(bid: str, body: BroadcastTestBody, user=Depends(require("broadcast"))):
        from ..broadcast import BroadcastError
        try:
            await app.broadcasts.test(bid, body.telegram_id)
        except BroadcastError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @api.delete("/api/broadcasts/{bid}")
    async def broadcasts_delete(bid: str, user=Depends(require("broadcast"))):
        return {"ok": await app.broadcasts.delete(bid)}

    @api.post("/api/cache/clear")
    async def cache_clear(user=Depends(require("cache"))):
        n = await app.cache.clear()
        await app.catalog.bump()
        return {"cleared": n}

    @api.get("/api/logs")
    async def logs(level: str | None = None, limit: int = 100, user=Depends(require("view"))):
        return J(recent_logs(min(limit, 300), level if level in ("ERROR", "WARNING", "INFO") else None))

    @api.get("/api/backups")
    async def backups(user=Depends(require("backups"))):
        return J(list_backups(s.backup_dir))

    @api.post("/api/backups")
    async def backup_create(user=Depends(require("backups"))):
        r = await create_backup(app.db, s.backup_dir)
        return J({k: v for k, v in r.items() if k != "path"})

    @api.get("/api/backups/{name}")
    async def backup_download(name: str, user=Depends(require("backups"))):
        if not re.fullmatch(r"ajcine-\d{8}-\d{6}\.json\.gz", name):
            raise HTTPException(400, "bad name")
        p = Path(s.backup_dir) / name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, filename=name, media_type="application/gzip")

    # ---------------------------------------------------------------- admins
    @api.get("/api/admins")
    async def admins_list(user=Depends(require("admins"))):
        return J([d async for d in app.db[dbm.ADMINS].find({})])

    @api.post("/api/admins")
    async def admin_create(body: AdminBody, user=Depends(require("admins"))):
        if await app.db[dbm.ADMINS].find_one({"username": body.username}):
            raise HTTPException(409, "username exists")
        await app.db[dbm.ADMINS].insert_one({"username": body.username, "password_hash": hash_password(body.password),
                                             "role": body.role, "active": True, "created_at": now(),
                                             "created_by": user["username"]})
        return {"ok": True}

    @api.delete("/api/admins/{username}")
    async def admin_delete(username: str, user=Depends(require("admins"))):
        if username == user["username"]:
            raise HTTPException(400, "you cannot remove yourself")
        r = await app.db[dbm.ADMINS].update_one({"username": username}, {"$set": {"active": False}})
        await app.db[dbm.WEB_SESSIONS].delete_many({"username": username})
        return {"ok": bool(r.matched_count)}

    @api.post("/api/telegram-admins")
    async def tg_admin_add(body: TgAdminBody, user=Depends(require("admins"))):
        await app.users.grant_telegram_admin(body.telegram_id, body.role)
        return {"ok": True}

    @api.delete("/api/telegram-admins/{tid}")
    async def tg_admin_del(tid: int, user=Depends(require("admins"))):
        return {"ok": await app.users.revoke_telegram_admin(tid)}

    # ---------------------------------------------------------------- dashboard
    api.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @api.get("/admin")
    @api.get("/admin/")
    async def admin_index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- public website
    from .public import build_public_router
    api.include_router(build_public_router(app))
    ASSETS = SITE / "assets"

    if not SITE.is_dir():
        raise RuntimeError(f"Website directory not found: {SITE}")
    
    if not (SITE / "index.html").is_file():
        raise RuntimeError(f"Website index.html not found: {SITE / 'index.html'}")
    
    if not ASSETS.is_dir():
        raise RuntimeError(f"Website assets directory not found: {ASSETS}")
    
    api.mount(
        "/assets",
        StaticFiles(
            directory=str(ASSETS),
            check_dir=True,
            html=False
        ),
        name="site-assets"
    )

    async def site_page():
        cfg = await site_config(app)
        if not cfg["public"]:
            return Response("The website is switched off.", status_code=404, media_type="text/plain")
        return FileResponse(SITE / "index.html", headers={"Cache-Control": "no-cache"})

    for route in ("/", "/search", "/browse", "/movies", "/series", "/anime", "/library", "/settings", "/title/{title_id}", "/watch/{token}"):
        api.add_api_route(route, site_page, methods=["GET"], include_in_schema=False)

    return api
