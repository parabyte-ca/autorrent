import asyncio
import logging
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Download, DownloadHistory, DownloadPath, Setting
from ..schemas import DownloadCreate, DuplicateCheckRequest
from ..services.duplicate import check_duplicate
from ..services.media_servers import trigger_media_refresh
from ..services.qbittorrent import _hash_from_magnet, add_torrent, get_torrent_status

logger = logging.getLogger(__name__)
router = APIRouter()

DONE_STATES = {"uploading", "stalledUP", "forcedUP", "checkingUP"}
SEEDING_STATES = {"uploading", "stalledUP", "forcedUP"}
DOWNLOADING_STATES = {"downloading", "stalledDL", "forcedDL", "checkingDL", "metaDL"}
ERROR_STATES = {"error", "missingFiles"}


def _fire_media_refresh(settings_dict: dict) -> None:
    """Spawn a daemon thread to run the media refresh without blocking the response."""
    def _run():
        try:
            asyncio.run(trigger_media_refresh(settings_dict))
        except Exception as e:
            logger.error("Media refresh thread error: %s", e)

    threading.Thread(target=_run, daemon=True).start()


def _update_history_completed(db: Session, torrent_hash: str, size_bytes: int | None) -> None:
    """Mark the most recent matching history row as completed."""
    try:
        hist = (
            db.query(DownloadHistory)
            .filter(
                DownloadHistory.torrent_hash == torrent_hash,
                DownloadHistory.status == "downloading",
            )
            .order_by(DownloadHistory.added_at.desc())
            .first()
        )
        if hist:
            hist.status = "completed"
            hist.completed_at = datetime.utcnow()
            if hist.size_bytes is None and size_bytes:
                hist.size_bytes = size_bytes
    except Exception as e:
        logger.error("Failed to update history on completion: %s", e)


def _update_history_failed(db: Session, torrent_hash: str, error_msg: str) -> None:
    """Mark the most recent matching history row as failed."""
    try:
        hist = (
            db.query(DownloadHistory)
            .filter(
                DownloadHistory.torrent_hash == torrent_hash,
                DownloadHistory.status == "downloading",
            )
            .order_by(DownloadHistory.added_at.desc())
            .first()
        )
        if hist:
            hist.status = "failed"
            hist.error_msg = error_msg
    except Exception as e:
        logger.error("Failed to update history on failure: %s", e)


@router.get("/downloads")
def get_downloads(db: Session = Depends(get_db)):
    downloads = db.query(Download).order_by(Download.created_at.desc()).all()
    result = []

    for d in downloads:
        item = {
            "id": d.id,
            "title": d.title,
            "torrent_hash": d.torrent_hash,
            "size_bytes": d.size_bytes,
            "status": d.status,
            "download_path": d.download_path,
            "watchlist_id": d.watchlist_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "progress": None,
            "eta": None,
            "dlspeed": None,
        }

        if d.torrent_hash and d.status not in ("done", "error"):
            prev_status = d.status
            try:
                qs = get_torrent_status(d.torrent_hash)
                if qs:
                    item["progress"] = qs.get("progress")
                    item["eta"] = qs.get("eta")
                    item["dlspeed"] = qs.get("dlspeed")
                    if d.size_bytes is None and qs.get("size"):
                        d.size_bytes = qs["size"]
                    qstate = qs.get("status", "")
                    if qstate in SEEDING_STATES:
                        d.status = "seeding"
                        item["status"] = "seeding"
                        if prev_status != "seeding":
                            _update_history_completed(db, d.torrent_hash, d.size_bytes)
                            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
                            _fire_media_refresh(settings_dict)
                    elif qstate in DOWNLOADING_STATES:
                        d.status = "downloading"
                        item["status"] = "downloading"
                    elif qstate in ERROR_STATES:
                        d.status = "error"
                        item["status"] = "error"
                        if prev_status != "error":
                            _update_history_failed(db, d.torrent_hash, qstate)
                    db.commit()
            except Exception:
                pass

        result.append(item)

    return result


@router.post("/downloads/check-duplicate")
def check_duplicate_endpoint(payload: DuplicateCheckRequest, db: Session = Depends(get_db)):
    """Check whether a torrent appears to have been downloaded before.

    Always returns HTTP 200 — callers interpret the ``is_duplicate`` field.
    """
    return check_duplicate(
        torrent_hash=payload.torrent_hash,
        torrent_name=payload.torrent_name,
        db=db,
    )


@router.post("/downloads", status_code=201)
def add_download(payload: DownloadCreate, db: Session = Depends(get_db)):
    dp = None
    save_path = "/downloads"

    if payload.download_path_id:
        dp = db.query(DownloadPath).filter(DownloadPath.id == payload.download_path_id).first()
        if dp:
            save_path = dp.path
    else:
        dp = db.query(DownloadPath).filter(DownloadPath.is_default == True).first()
        if dp:
            save_path = dp.path

    settings = {s.key: s.value for s in db.query(Setting).all()}
    category = settings.get("qbit_category", "autorrent")

    # Duplicate detection — derive hash from the magnet link.
    torrent_hash = _hash_from_magnet(payload.magnet)
    dup = check_duplicate(torrent_hash=torrent_hash, torrent_name=payload.title, db=db)

    if dup["is_duplicate"] and not payload.force:
        raise HTTPException(
            status_code=409,
            detail={
                "duplicate": True,
                "match_type": dup["match_type"],
                "matched_name": dup["matched_name"],
                "matched_at": dup["matched_at"],
                "message": "This torrent appears to have already been downloaded.",
            },
        )
    if dup["is_duplicate"] and payload.force:
        logger.info(
            "Duplicate override: adding '%s' despite existing match (%s).",
            payload.title, dup["match_type"],
        )

    try:
        info_hash = add_torrent(payload.magnet, save_path, category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add torrent: {e}")

    download = Download(
        title=payload.title,
        torrent_hash=info_hash,
        magnet_link=payload.magnet,
        status="downloading",
        download_path=save_path,
        watchlist_id=payload.watchlist_id,
    )
    db.add(download)
    db.commit()
    db.refresh(download)

    # Write history record immediately after the torrent is accepted by
    # qBittorrent.  Failures here must never block or fail the download itself.
    try:
        hist = DownloadHistory(
            name=payload.title,
            source="manual",
            indexer=payload.indexer,
            folder=dp.name if dp else None,
            torrent_hash=info_hash,
            size_bytes=None,  # not known at add time; filled in on completion
            status="downloading",
            watchlist_id=payload.watchlist_id,
        )
        db.add(hist)
        db.commit()
    except Exception as e:
        logger.error("Failed to write download history: %s", e)

    return {
        "id": download.id,
        "title": download.title,
        "torrent_hash": download.torrent_hash,
        "status": download.status,
        "download_path": download.download_path,
        "created_at": download.created_at.isoformat() if download.created_at else None,
    }


@router.delete("/downloads/{download_id}")
def delete_download(download_id: int, db: Session = Depends(get_db)):
    download = db.query(Download).filter(Download.id == download_id).first()
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    db.delete(download)
    db.commit()
    return {"ok": True}
