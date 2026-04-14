"""
backup.py — Export and restore AutoRrent configuration and data.

Data included in every backup ZIP
──────────────────────────────────
  autorrent.db      The live SQLite database — the authoritative source of
                    truth for all application state (settings, watchlist,
                    downloads, paths).  Filesystem path is derived from the
                    DATABASE_URL environment variable; the default inside the
                    Docker container is /app/data/autorrent.db.

  settings.json     Human-readable snapshot of the 'settings' table at the
                    moment of export.  Intended for disaster-recovery reference
                    only — when restoring, the database file is overwritten
                    directly; this file is not re-imported separately.

  backup_meta.json  Metadata: creation timestamp (ISO 8601 UTC), app version
                    (from APP_VERSION env var), and the original DB path.

No persistent configuration files exist outside the database.  All user
settings are stored in the 'settings' table within the SQLite database, so a
single-file backup of autorrent.db captures everything.
"""
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import DATABASE_URL, get_db
from ..models import Setting
from ..services.scheduler import get_scheduler

logger = logging.getLogger(__name__)
router = APIRouter()

# Derive the filesystem path of the SQLite file from the SQLAlchemy URL.
# DATABASE_URL uses 'sqlite:////' (four slashes) for an absolute path on POSIX.
# Removing the three-slash prefix 'sqlite:///' leaves the absolute path intact.
#   "sqlite:////app/data/autorrent.db"  →  "/app/data/autorrent.db"
_DB_PATH: str = DATABASE_URL.replace("sqlite:///", "", 1)


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/backup/export")
def export_backup(db: Session = Depends(get_db)):
    """Stream an in-memory ZIP containing the database and a settings snapshot."""
    settings_dict = {s.key: s.value for s in db.query(Setting).all()}

    now_utc = datetime.now(timezone.utc)
    meta = {
        "created_at": now_utc.isoformat(),
        "app_version": os.environ.get("APP_VERSION", "dev"),
        "db_path": _DB_PATH,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(_DB_PATH):
            zf.write(_DB_PATH, "autorrent.db")
        else:
            logger.warning(
                "DB file not found at %s — backup will not include autorrent.db",
                _DB_PATH,
            )

        zf.writestr("settings.json", json.dumps(settings_dict, indent=2))
        zf.writestr("backup_meta.json", json.dumps(meta, indent=2))

    buf.seek(0)
    filename = f"autorrent-backup-{now_utc.strftime('%Y-%m-%d')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Restore ───────────────────────────────────────────────────────────────────


@router.post("/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    """Replace the live database with the autorrent.db found in a backup ZIP.

    Restore procedure
    -----------------
    1. Validate the uploaded file is a ZIP containing the required entries.
    2. Pause the APScheduler so no jobs run while the database is being swapped.
       ``pause()``/``resume()`` is used instead of ``shutdown()``/``start()``
       because APScheduler 3.x schedulers cannot be restarted after shutdown.
    3. Write the extracted database to a temporary path alongside the live file,
       then perform an atomic ``os.replace()`` to swap it in.
    4. Resume the scheduler in a ``finally`` block so it is always restarted,
       even if the restore itself fails mid-way.
    """
    content = await file.read()

    # ── Validate ──────────────────────────────────────────────────────────────
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail="Invalid ZIP file — the uploaded file could not be opened as a ZIP archive.",
        )

    with zf:
        names = zf.namelist()

        if "autorrent.db" not in names:
            raise HTTPException(
                status_code=400,
                detail="Invalid backup: 'autorrent.db' is missing from the ZIP.",
            )
        if "backup_meta.json" not in names:
            raise HTTPException(
                status_code=400,
                detail="Invalid backup: 'backup_meta.json' is missing from the ZIP.",
            )

        try:
            meta = json.loads(zf.read("backup_meta.json"))
        except (json.JSONDecodeError, KeyError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid backup: could not parse backup_meta.json — {exc}",
            )

        # ── Restore ───────────────────────────────────────────────────────────
        scheduler = get_scheduler()
        # STATE_RUNNING == 1 in APScheduler 3.x; only pause when actively running
        # (not already paused or stopped) so we don't upset a pre-existing state.
        paused_by_us = False
        tmp_path = _DB_PATH + ".restore_tmp"

        try:
            if scheduler.state == 1:
                scheduler.pause()
                paused_by_us = True
                logger.info("Scheduler paused for database restore")

            db_bytes = zf.read("autorrent.db")

            # Write to a sibling temp file first so the live DB is never left
            # in a partially overwritten state if we are interrupted.
            with open(tmp_path, "wb") as fh:
                fh.write(db_bytes)

            # os.replace() is atomic on Linux/POSIX — safe inside Docker.
            os.replace(tmp_path, _DB_PATH)
            logger.info(
                "Database restored from backup (backup created: %s)",
                meta.get("created_at", "unknown"),
            )

        except Exception as exc:
            # Clean up the temp file if it was left behind.
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            logger.error("Database restore failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"Restore failed: {exc}")

        finally:
            # Always resume the scheduler — even on failure the app must keep
            # functioning so the user can retry or investigate.
            if paused_by_us:
                try:
                    scheduler.resume()
                    logger.info("Scheduler resumed after restore")
                except Exception as exc:
                    logger.error("Failed to resume scheduler after restore: %s", exc)

    return {
        "ok": True,
        "restored_from": meta.get("created_at", ""),
        "message": "Restore complete. Please refresh the page.",
    }
