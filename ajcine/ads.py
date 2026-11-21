"""Advertisements: managed by administrators, always labelled "Sponsored".

An ad has a title, short text, an image URL (https, never downloaded or stored), a link and where it may appear:
  bot_results   a "Sponsored" link button under search results
  bot_delivery  a sponsored message (image + text + button) after a file is delivered
  site_banner   a banner on the public website
Impressions and clicks are counted per ad. Clicks go through `/go/ad/<id>` when PUBLIC_BASE_URL is set.
Ads can be switched off for everyone at any time.
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from . import db as dbm

PLACEMENTS = ("bot_results", "bot_delivery", "site_banner")
URL_RE = re.compile(r"^https://[^\s]{4,500}$")
KEY = "ads_config"


def now() -> datetime:
    return datetime.now(timezone.utc)


class AdError(Exception):
    pass


def clean_ad(data: dict) -> dict:
    """Validate and normalise ad fields (raises AdError)."""
    title = " ".join(str(data.get("title") or "").split())[:80]
    text = str(data.get("text") or "").strip()[:300]
    link = str(data.get("link_url") or "").strip()
    image = str(data.get("image_url") or "").strip()
    button = " ".join(str(data.get("button_text") or "Learn more").split())[:24] or "Learn more"
    places = [p for p in dict.fromkeys(data.get("placements") or []) if p in PLACEMENTS]
    if len(title) < 2:
        raise AdError("Give the ad a title.")
    if not URL_RE.match(link):
        raise AdError("The link must be a full https:// address.")
    if image and not URL_RE.match(image):
        raise AdError("The image must be a direct https:// image address.")
    if not places:
        raise AdError("Choose where the ad appears.")
    if "site_banner" in places and not image:
        raise AdError("The website banner needs an image address.")
    weight = int(data.get("weight") or 1)
    if not 1 <= weight <= 10:
        raise AdError("Weight must be between 1 and 10.")
    out = {"title": title, "text": text, "link_url": link, "image_url": image or None, "button_text": button,
           "placements": places, "weight": weight, "active": bool(data.get("active", True)),
           "starts_at": _dt(data.get("starts_at")), "ends_at": _dt(data.get("ends_at"))}
    if out["starts_at"] and out["ends_at"] and out["ends_at"] <= out["starts_at"]:
        raise AdError("The end date must be after the start date.")
    return out


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise AdError("Dates must look like 2026-12-31.")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class AdService:
    def __init__(self, settings: Any, db: Any, cache: Any):
        self.s, self.db, self.cache = settings, db, cache

    async def enabled(self) -> bool:
        over = await dbm.get_setting(self.db, KEY, {}) or {}
        return bool(over.get("enabled", self.s.ads_enabled))

    async def set_enabled(self, on: bool) -> None:
        await dbm.set_setting(self.db, KEY, {"enabled": bool(on)})

    # ---- admin CRUD ------------------------------------------------------------------------------
    async def create(self, data: dict, actor: str) -> dict:
        doc = {**clean_ad(data), "impressions": 0, "clicks": 0, "created_by": actor, "created_at": now()}
        r = await self.db[dbm.ADS].insert_one(doc)
        doc["_id"] = str(r.inserted_id)
        return doc

    async def update(self, ad_id: str, data: dict) -> bool:
        r = await self.db[dbm.ADS].update_one({"_id": _oid(ad_id)}, {"$set": {**clean_ad(data), "updated_at": now()}})
        return bool(r.matched_count)

    async def set_active(self, ad_id: str, active: bool) -> bool:
        r = await self.db[dbm.ADS].update_one({"_id": _oid(ad_id)}, {"$set": {"active": bool(active)}})
        return bool(r.matched_count)

    async def delete(self, ad_id: str) -> bool:
        return bool((await self.db[dbm.ADS].delete_one({"_id": _oid(ad_id)})).deleted_count)

    async def listing(self) -> list[dict]:
        out = []
        async for d in self.db[dbm.ADS].find({}).sort("created_at", -1).limit(200):
            d["_id"] = str(d["_id"])
            d["ctr"] = round(100 * d.get("clicks", 0) / d["impressions"], 1) if d.get("impressions") else None
            out.append(d)
        return out

    async def get(self, ad_id: str) -> dict | None:
        d = await self.db[dbm.ADS].find_one({"_id": _oid(ad_id)})
        if d:
            d["_id"] = str(d["_id"])
        return d

    # ---- serving ----------------------------------------------------------------------------------
    async def pick(self, placement: str) -> dict | None:
        """One live ad for a placement (weighted random), or None."""
        if placement not in PLACEMENTS or not await self.enabled():
            return None
        t = now()
        live = []
        async for d in self.db[dbm.ADS].find({"active": True, "placements": placement}):
            s, e = d.get("starts_at"), d.get("ends_at")
            if s and (s if s.tzinfo else s.replace(tzinfo=timezone.utc)) > t:
                continue
            if e and (e if e.tzinfo else e.replace(tzinfo=timezone.utc)) <= t:
                continue
            live.append(d)
        if not live:
            return None
        d = random.choices(live, weights=[x.get("weight", 1) for x in live])[0]
        d["_id"] = str(d["_id"])
        return d

    async def impression(self, ad_id: str) -> None:
        await self.db[dbm.ADS].update_one({"_id": _oid(ad_id)}, {"$inc": {"impressions": 1}})

    async def click(self, ad_id: str) -> str | None:
        """Count a click and return the destination."""
        d = await self.db[dbm.ADS].find_one_and_update({"_id": _oid(ad_id), "active": True}, {"$inc": {"clicks": 1}})
        return d["link_url"] if d else None

    def link_for(self, ad: dict) -> str:
        """Trackable link when the site has a public address, else the destination itself."""
        base = self.s.public_base_url
        return f"{base}/go/ad/{ad['_id']}" if base else ad["link_url"]

    async def bot_cooldown_ok(self, user_id: int) -> bool:
        """True at most once per cooldown per user (the ad message after a delivery)."""
        return await self.cache.incr(f"adcd:{user_id}", self.s.ads_bot_cooldown_minutes * 60) == 1


def _oid(v: str) -> ObjectId:
    try:
        return ObjectId(v)
    except Exception:
        return ObjectId("0" * 24)
