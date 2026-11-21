
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from .. import db as dbm

log = logging.getLogger("ajcine.imdb.dataset")

DATASET_FILES = [
    "name.basics", "title.akas", "title.basics", "title.crew",
    "title.episode", "title.principals", "title.ratings",
]
STATE_KEY = "imdb_dataset"
MANIFEST_NAME = "cinemagoer-import-manifest.json"


def sqlite_path(uri: str) -> str | None:
    """Return the file path for a canonical sqlite: URI, else None."""
    prefix = "sqlite:///"
    if uri.startswith(prefix):
        return uri[len(prefix):]
    return None


class DatasetManager:
    def __init__(self, settings: Settings, db: Any, imdb_service: Any):
        self.settings = settings
        self.db = db
        self.imdb = imdb_service
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.dir = Path(settings.imdb_dataset_dir)

    # ---- state ------------------------------------------------------------------
    async def _state(self) -> dict:
        return await dbm.get_setting(self.db, STATE_KEY, {}) or {}

    async def _patch(self, **kw) -> dict:
        st = await self._state()
        st.update(kw)
        st["updated_at"] = _iso()
        await dbm.set_setting(self.db, STATE_KEY, st)
        return st

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def status(self) -> dict:
        st = await self._state()
        path = sqlite_path(self.settings.imdb_db_uri)
        files = []
        for name in DATASET_FILES:
            f = self.dir / f"{name}.tsv.gz"
            files.append({"name": f.name, "present": f.exists(), "bytes": f.stat().st_size if f.exists() else 0})
        return {
            "state": "running" if self.running else st.get("state", "idle"),
            "step": st.get("step"),
            "detail": st.get("detail"),
            "last_download_at": st.get("last_download_at"),
            "last_import_at": st.get("last_import_at"),
            "last_error": st.get("last_error"),
            "manifest": st.get("manifest"),
            "db_uri_kind": "sqlite" if path else "sqlalchemy",
            "db_present": bool(path and os.path.exists(path)),
            "db_bytes": os.path.getsize(path) if path and os.path.exists(path) else 0,
            "files": files,
            "update_days": self.settings.imdb_update_days,
        }

    async def needs_update(self) -> bool:
        st = await self._state()
        path = sqlite_path(self.settings.imdb_db_uri)
        if path and not os.path.exists(path):
            return True
        last = st.get("last_import_at")
        if not last:
            return True
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        except ValueError:
            return True
        return age.days >= self.settings.imdb_update_days

    # ---- health -------------------------------------------------------------------
    async def health(self) -> dict:
        probe = await self.imdb.probe()
        st = await self.status()
        return {
            "ok": bool(probe.get("ok")),
            "probe": probe,
            "db_present": st["db_present"],
            "db_bytes": st["db_bytes"],
            "last_import_at": st["last_import_at"],
            "manifest_status": (st.get("manifest") or {}).get("status"),
        }

    # ---- download ------------------------------------------------------------------
    async def download(self, force: bool = False) -> None:
        import aiohttp

        self.dir.mkdir(parents=True, exist_ok=True)
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "AJCineAI/1.0"}) as http:
            for i, name in enumerate(DATASET_FILES, 1):
                dest = self.dir / f"{name}.tsv.gz"
                fresh = dest.exists() and (time.time() - dest.stat().st_mtime) < 3600 * 24 * 2
                if fresh and not force:
                    continue
                await self._patch(state="downloading", step=f"download {i}/{len(DATASET_FILES)}", detail=dest.name)
                url = self.settings.imdb_dataset_base_url.rstrip("/") + f"/{dest.name}"
                part = dest.with_suffix(dest.suffix + ".part")
                async with http.get(url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"download {dest.name}: HTTP {resp.status}")
                    expected = int(resp.headers.get("Content-Length") or 0)
                    written = 0
                    with open(part, "wb") as fh:
                        async for chunk in resp.content.iter_chunked(1 << 20):
                            fh.write(chunk)
                            written += len(chunk)
                if expected and written != expected:
                    part.unlink(missing_ok=True)
                    raise RuntimeError(f"download {dest.name}: truncated ({written}/{expected})")
                await asyncio.to_thread(self._verify_gzip, part)
                os.replace(part, dest)
        await self._patch(last_download_at=_iso())

    @staticmethod
    def _verify_gzip(path: Path) -> None:
        with gzip.open(path, "rb") as fh:
            while fh.read(1 << 22):
                pass

    # ---- import ----------------------------------------------------------------------
    @staticmethod
    def _importer_script() -> str:
        found = shutil.which("s32cinemagoer.py")
        if found:
            return found
        cand = os.path.join(sysconfig.get_path("scripts"), "s32cinemagoer.py")
        if os.path.exists(cand):
            return cand
        raise RuntimeError("s32cinemagoer.py not found; install cinemagoer>=2026.6")

    async def import_dataset(self) -> dict:
        missing = [n for n in DATASET_FILES if not (self.dir / f"{n}.tsv.gz").exists()]
        if missing:
            raise RuntimeError(f"dataset files missing: {', '.join(missing)}")
        script = self._importer_script()
        live_path = sqlite_path(self.settings.imdb_db_uri)
        if live_path:
            Path(live_path).parent.mkdir(parents=True, exist_ok=True)
            staging_path = live_path + ".new"
            Path(staging_path).unlink(missing_ok=True)
            target_uri = "sqlite:///" + staging_path
        else:
            target_uri = self.settings.imdb_db_uri  # SQLAlchemy URI: importer replaces in place

        await self._patch(state="importing", step="import", detail="running s32cinemagoer (can take ~1 hour)")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script, str(self.dir), target_uri,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        tail: list[str] = []
        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail = (tail + [text])[-20:]
                await self._patch(detail=text[:200])
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"importer exited with {rc}: {' | '.join(tail[-3:])}"[:600])

        manifest = self._read_manifest()
        if manifest is not None and str(manifest.get("status", "")).lower() != "completed":
            raise RuntimeError(f"import manifest status is {manifest.get('status')!r}")

        if live_path:
            if not os.path.exists(staging_path) or os.path.getsize(staging_path) < 1024:
                raise RuntimeError("import produced no database")
            await self.imdb.reload()  # release the old file handle before replacing
            os.replace(staging_path, live_path)
            await self.imdb.reload()
        await self._patch(last_import_at=_iso(), manifest=manifest)
        return manifest or {"status": "completed"}

    def _read_manifest(self) -> dict | None:
        candidates = [self.dir / MANIFEST_NAME, Path(MANIFEST_NAME)]
        live = sqlite_path(self.settings.imdb_db_uri)
        if live:
            candidates += [Path(live).parent / MANIFEST_NAME, Path(live + ".new").parent / MANIFEST_NAME]
        for c in candidates:
            try:
                return json.loads(c.read_text())
            except (OSError, ValueError):
                continue
        return None

    # ---- orchestration ------------------------------------------------------------------
    async def update(self, force: bool = False, skip_download: bool = False) -> None:
        """Download + import. Guarded so only one update runs at a time."""
        if self._lock.locked():
            raise RuntimeError("an IMDb dataset update is already running")
        async with self._lock:
            try:
                if not skip_download:
                    await self.download(force=force)
                await self.import_dataset()
                await self._patch(state="idle", step=None, detail="completed", last_error=None)
                log.info("IMDb dataset update completed")
            except Exception as exc:
                log.error("IMDb dataset update failed: %s", exc)
                await self._patch(state="error", last_error=str(exc)[:600])
                raise

    def start_background(self, force: bool = False, skip_download: bool = False) -> bool:
        if self.running or self._lock.locked():
            return False

        async def runner():
            try:
                await self.update(force=force, skip_download=skip_download)
            except Exception:
                pass  # already logged and persisted

        self._task = asyncio.create_task(runner(), name="imdb-dataset-update")
        return True

    async def scheduler(self) -> None:
        """Check once a day whether the dataset is older than IMDB_UPDATE_DAYS."""
        while True:
            try:
                if not self.running and await self.needs_update():
                    log.info("IMDb dataset is stale or missing; starting update")
                    self.start_background()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("dataset scheduler error: %s", exc)
            await asyncio.sleep(24 * 3600)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()
