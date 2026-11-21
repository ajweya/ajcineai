"""Manual review and correction service (shared by the web API and Telegram panel).

Every manual action is recorded permanently in `manual_corrections` and takes
priority over automatic identification on all later indexing and re-indexing.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import db as dbm
from .catalog import Catalog, coll_for
from .identify.pipeline import Identification, IdentificationPipeline
from .matching.models import Candidate, MatchQuery
from .matching.service import TitleService, valid_imdb_id
from .parsing.languages import lang_code
from .parsing.parser import ParsedName
from .parsing.text import match_key, tokens_for_index

log = logging.getLogger("ajcine.review")

LOCAL_PREFIX = "loc_"
EDITABLE_FILE_FIELDS = {"season", "episode", "episode_title", "quality", "languages", "content_type"}
EDITABLE_TITLE_FIELDS = {"title", "year", "original_title", "genres", "overview", "kind"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def valid_title_id(v: str | None) -> bool:
    return bool(v and (valid_imdb_id(v) or re.fullmatch(rf"{LOCAL_PREFIX}[0-9a-f]{{10}}", v)))


def parsed_from_dict(d: dict | None) -> ParsedName:
    d = d or {}
    return ParsedName(**{k: v for k, v in d.items() if k in ParsedName.__dataclass_fields__})


class ReviewError(ValueError):
    pass


class ReviewService:
    def __init__(self, db: Any, catalog: Catalog, lookup: TitleService, pipeline: IdentificationPipeline):
        self.db = db
        self.catalog = catalog
        self.lookup = lookup
        self.pipeline = pipeline

    # ---- reading -------------------------------------------------------------------
    async def queue(self, status: str = "pending", page: int = 1, per_page: int = 20, q: str | None = None) -> dict:
        f: dict = {"status": status}
        if q:
            f["parsed.title"] = {"$regex": re.escape(q), "$options": "i"}
        total = await self.db[dbm.QUEUE].count_documents(f)
        cur = self.db[dbm.QUEUE].find(f).sort("created_at", 1).skip(max(0, page - 1) * per_page).limit(per_page)
        items = []
        async for it in cur:
            fd = await self.db[dbm.FILES].find_one({"_id": it["file_id"]}, {"file_name": 1, "size": 1, "caption": 1})
            items.append({
                "file_id": it["file_id"], "file_name": (fd or {}).get("file_name"),
                "size": (fd or {}).get("size"), "reason": it.get("reason"),
                "parsed": it.get("parsed"), "top": (it.get("candidates") or [None])[0],
                "candidates": len(it.get("candidates") or []), "ai_used": bool(it.get("ai")),
                "created_at": it.get("created_at"),
            })
        return {"total": total, "page": page, "per_page": per_page, "items": items}

    async def item(self, file_id: str) -> dict:
        fd = await self.db[dbm.FILES].find_one({"_id": file_id})
        if not fd:
            raise ReviewError("file not found")
        qd = await self.db[dbm.QUEUE].find_one({"file_id": file_id}) or {}
        return {"file": _public_file(fd), "candidates": qd.get("candidates") or [], "ai": qd.get("ai"),
                "reason": qd.get("reason"), "parsed": fd.get("parsed"), "queue_status": qd.get("status")}

    async def search_titles(self, text: str, year: int | None = None, kind: str | None = None) -> list[dict]:
        if not text.strip():
            return []
        ranked = await self.lookup.search(MatchQuery(text.strip(), year, kind), limit=15)
        return [c.to_dict() for c in ranked[:10]]

    # ---- history ---------------------------------------------------------------------------
    async def _record(self, type_: str, actor: dict, **fields) -> None:
        await self.db[dbm.CORRECTIONS].insert_one({"type": type_, "actor": actor, "created_at": now(),
                                                   "active": True, **fields})

    async def history(self, limit: int = 100, file_unique_id: str | None = None, title_id: str | None = None) -> list[dict]:
        f: dict = {}
        if file_unique_id:
            f["file_unique_id"] = file_unique_id
        if title_id:
            f["imdb_id"] = title_id
        cur = self.db[dbm.CORRECTIONS].find(f).sort("created_at", -1).limit(limit)
        out = []
        async for d in cur:
            d["_id"] = str(d["_id"])
            out.append(d)
        return out

    # ---- approve ---------------------------------------------------------------------------------
    async def _files_in_scope(self, fd: dict, scope: str) -> list[dict]:
        if scope == "file":
            return [fd]
        key = fd.get("title_key")
        if not key:
            return [fd]
        cur = self.db[dbm.FILES].find(
            {"title_key": key, "deleted": {"$ne": True}, "status": {"$in": ["pending_review", "unidentified"]}})
        files = [d async for d in cur]
        return files or [fd]

    async def _apply(self, files: list[dict], cand: Candidate, method: str, overrides: dict | None = None) -> int:
        n = 0
        touched_old: set[str] = set()
        for f in files:
            if f.get("title_id") and f["title_id"] != cand.imdb_id:
                touched_old.add(f["title_id"])
            p = parsed_from_dict(f.get("parsed"))
            ident = Identification("identified", method, p, title_id=cand.imdb_id, content_type=cand.kind,
                                   confidence=1.0, reason="manual approval", overrides=overrides or {})
            await self.pipeline.apply_to_file(f["_id"], ident)
            n += 1
        for old in touched_old:
            await self.catalog.refresh_title_summary(old)
            await self.catalog.prune_title_if_empty(old)
        await self.catalog.bump()
        return n

    async def approve(self, file_id: str, imdb_id: str, actor: dict, scope: str = "group",
                      overrides: dict | None = None) -> dict:
        if not valid_imdb_id(imdb_id):
            raise ReviewError("invalid IMDb id")
        fd = await self.db[dbm.FILES].find_one({"_id": file_id})
        if not fd:
            raise ReviewError("file not found")
        cand = await self.lookup.get(imdb_id)
        if cand is None:
            raise ReviewError("IMDb id not found in the local dataset")
        await self.catalog.upsert_title(cand, manual=True)
        files = await self._files_in_scope(fd, scope)
        n = await self._apply(files, cand, "manual", overrides)
        key = fd.get("title_key")
        if scope == "group" and key:
            await self._record("candidate", actor, title_key=key, imdb_id=imdb_id, kind=cand.kind,
                               title=cand.title, year=cand.year, file_unique_id=None, overrides=overrides or None)
            await self.db[dbm.TITLE_DECISIONS].update_one(
                {"title_key": key}, {"$set": {"title_key": key, "status": "identified", "imdb_id": imdb_id,
                                              "title": cand.title, "year": cand.year, "kind": cand.kind,
                                              "method": "manual", "confidence": 1.0, "updated_at": now()}}, upsert=True)
        else:
            await self._record("candidate", actor, file_unique_id=fd.get("file_unique_id"), imdb_id=imdb_id,
                               kind=cand.kind, title=cand.title, year=cand.year, overrides=overrides or None)
        return {"approved": n, "title_id": imdb_id, "title": cand.title}

    async def approve_local(self, file_id: str, title: str, year: int | None, kind: str, actor: dict,
                            scope: str = "group") -> dict:
        """For titles IMDb does not know: creates a clearly-local title (no fake IMDb data)."""
        title = title.strip()
        if not title or kind not in ("movie", "series"):
            raise ReviewError("title and kind (movie|series) are required")
        fd = await self.db[dbm.FILES].find_one({"_id": file_id})
        if not fd:
            raise ReviewError("file not found")
        tid = await self.catalog.resolve_local_id(kind, title, year)
        cand = Candidate(imdb_id=tid, title=title, year=year, kind=kind)
        await self.catalog.upsert_title(cand, manual=True)
        files = await self._files_in_scope(fd, scope)
        n = await self._apply(files, cand, "manual")
        key = fd.get("title_key")
        if scope == "group" and key:
            await self._record("local_title", actor, title_key=key, imdb_id=tid, kind=kind, title=title, year=year)
            await self.db[dbm.TITLE_DECISIONS].update_one(
                {"title_key": key}, {"$set": {"title_key": key, "status": "identified", "imdb_id": tid, "title": title,
                                              "year": year, "kind": kind, "method": "manual", "confidence": 1.0,
                                              "updated_at": now()}}, upsert=True)
        else:
            await self._record("local_title", actor, file_unique_id=fd.get("file_unique_id"), imdb_id=tid,
                               kind=kind, title=title, year=year)
        return {"approved": n, "title_id": tid}

    async def reject(self, file_id: str, actor: dict, scope: str = "file") -> dict:
        fd = await self.db[dbm.FILES].find_one({"_id": file_id})
        if not fd:
            raise ReviewError("file not found")
        files = await self._files_in_scope(fd, scope)
        ids = [f["_id"] for f in files]
        await self.db[dbm.FILES].update_many({"_id": {"$in": ids}}, {"$set": {"status": "rejected", "updated_at": now()}})
        await self.db[dbm.QUEUE].delete_many({"file_id": {"$in": ids}})
        await self._record("reject", actor, file_unique_id=fd.get("file_unique_id"), title_key=fd.get("title_key"),
                           imdb_id=None, count=len(ids))
        await self.catalog.bump()
        return {"rejected": len(ids)}

    # ---- edits --------------------------------------------------------------------------------------
    async def edit_file(self, file_id: str, fields: dict, actor: dict) -> dict:
        fd = await self.db[dbm.FILES].find_one({"_id": file_id})
        if not fd:
            raise ReviewError("file not found")
        upd: dict = {}
        for k, v in fields.items():
            if k not in EDITABLE_FILE_FIELDS:
                raise ReviewError(f"field not editable: {k}")
            if k in ("season", "episode"):
                if v in (None, ""):
                    upd[k] = None
                else:
                    try:
                        n = int(v)
                    except (TypeError, ValueError):
                        raise ReviewError(f"{k} must be a number")
                    if not 0 <= n <= (99 if k == "season" else 9999):
                        raise ReviewError(f"{k} out of range")
                    upd[k] = n
            elif k == "quality":
                q = str(v).strip().lower() if v else None
                if q in ("4k", "uhd"):
                    q = "2160p"
                if q and not re.fullmatch(r"\d{3,4}p|sd", q):
                    raise ReviewError("quality must look like 720p, 1080p, 2160p or SD")
                upd[k] = "SD" if q == "sd" else q
            elif k == "languages":
                vals = v if isinstance(v, list) else [x for x in re.split(r"[,+/ ]+", str(v)) if x]
                codes = [lang_code(x) for x in vals]
                if not codes or any(c is None for c in codes):
                    raise ReviewError("unknown language")
                upd[k] = list(dict.fromkeys(codes))
                upd["languages_source"] = "manual"
            elif k == "content_type":
                if v not in ("movie", "series"):
                    raise ReviewError("content_type must be movie or series")
                upd[k] = v
            elif k == "episode_title":
                upd[k] = (str(v).strip()[:200] or None) if v else None
        if not upd:
            raise ReviewError("nothing to change")
        upd["updated_at"] = now()
        before = {k: fd.get(k) for k in upd if k != "updated_at"}
        await self.db[dbm.FILES].update_one({"_id": file_id}, {"$set": upd})
        overrides = {k: v for k, v in upd.items() if k not in ("updated_at", "content_type", "languages_source")}
        await self._record("edit_file", actor, file_unique_id=fd.get("file_unique_id"), imdb_id=fd.get("title_id"),
                           kind=fd.get("content_type"), overrides=overrides, before=before)
        if fd.get("title_id"):
            await self.catalog.refresh_title_summary(fd["title_id"])
            await self.catalog.sync_seasons_episodes(fd["title_id"])
        return {"updated": list(overrides)}

    async def edit_title(self, title_id: str, fields: dict, actor: dict) -> dict:
        doc = await self.catalog.get_title(title_id)
        if not doc:
            raise ReviewError("title not found")
        upd: dict = {}
        for k, v in fields.items():
            if k not in EDITABLE_TITLE_FIELDS:
                raise ReviewError(f"field not editable: {k}")
            if k == "year":
                try:
                    upd["year"] = int(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    raise ReviewError("year must be a number")
                if upd["year"] is not None and not 1888 <= upd["year"] <= datetime.now().year + 2:
                    raise ReviewError("year out of range")
            elif k == "genres":
                vals = v if isinstance(v, list) else [x.strip() for x in str(v).split(",")]
                upd["genres"] = [x for x in vals if x][:8]
            elif k == "kind":
                if v not in ("movie", "series"):
                    raise ReviewError("kind must be movie or series")
                upd["kind"] = v
            else:
                upd[k] = (str(v).strip()[:2000] or None) if v else None
        if "title" in upd and not upd["title"]:
            raise ReviewError("title cannot be empty")
        new_kind = upd.pop("kind", None)
        merged = {**doc, **upd}
        upd["search_tokens"] = tokens_for_index(merged.get("title"), merged.get("original_title"), *merged.get("alt_titles", []))
        upd["manual"] = True
        upd["updated_at"] = now()
        coll = coll_for(doc["kind"])
        await self.db[coll].update_one({"_id": title_id}, {"$set": upd})
        if new_kind and new_kind != doc["kind"]:
            fresh = await self.db[coll].find_one({"_id": title_id})
            fresh["kind"] = new_kind
            await self.db[coll_for(doc["kind"])].delete_one({"_id": title_id})
            await self.db[coll_for(new_kind)].replace_one({"_id": title_id}, fresh, upsert=True)
            await self.db[dbm.FILES].update_many({"title_id": title_id}, {"$set": {"content_type": new_kind}})
        await self._record("edit_title", actor, imdb_id=title_id, after={k: v for k, v in upd.items() if k != "search_tokens"},
                           before={k: doc.get(k) for k in upd if k in doc})
        await self.catalog.bump()
        return {"updated": [k for k in upd if k not in ("search_tokens", "manual", "updated_at")] + (["kind"] if new_kind else [])}

    async def change_imdb_id(self, old_id: str, new_id: str, actor: dict) -> dict:
        """Re-point a title (and all its files) to a different IMDb id."""
        if not valid_imdb_id(new_id):
            raise ReviewError("invalid IMDb id")
        cand = await self.lookup.get(new_id)
        if cand is None:
            raise ReviewError("IMDb id not found in the local dataset")
        old = await self.catalog.get_title(old_id)
        if not old:
            raise ReviewError("title not found")
        await self.catalog.upsert_title(cand, manual=True, poster=old.get("poster"))
        files = [f async for f in self.db[dbm.FILES].find({"title_id": old_id})]
        n = await self._apply(files, cand, "manual")
        await self._record("change_imdb_id", actor, imdb_id=new_id, before={"imdb_id": old_id})
        await self.catalog.prune_title_if_empty(old_id)
        return {"moved": n, "title_id": new_id}

    async def merge(self, src: str, dst: str, actor: dict) -> dict:
        moved = await self.catalog.merge_titles(src, dst)
        await self._record("merge", actor, imdb_id=dst, before={"merged_from": src}, count=moved)
        return {"moved": moved}

    async def undo_correction(self, correction_id: str, actor: dict) -> bool:
        from bson import ObjectId
        r = await self.db[dbm.CORRECTIONS].update_one({"_id": ObjectId(correction_id)}, {"$set": {"active": False}})
        return bool(r.matched_count)


def _public_file(fd: dict) -> dict:
    keep = ("_id", "file_name", "caption", "size", "mime", "kind", "duration", "status", "title_id", "content_type",
            "season", "episode", "episode_title", "quality", "languages", "source_chat_id", "source_message_id",
            "indexed_at", "identification")
    return {k: fd.get(k) for k in keep if k in fd}
