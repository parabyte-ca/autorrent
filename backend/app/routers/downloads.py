import asyncio
import logging
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Download, DownloadHistory, DownloadPath, Setting
from ..schemas import DownloadCreate, DuplicateCheckRequest
from ..services.duplicate import check_duplicate
from ..services.media_servers import trigger_media_refresh
from ..services.qbittorrent import _hash_from_magnet, add_torrent, get_torrent_status, remove_torrent

logger = logging.getLogger(__name__)
router = APIRouter()

# All qBittorrent state strings that mean the torrent has finished downloading.
# Previously the code only checked a subset ("uploading", "stalledUP", "forcedUP"),
# which caused torrents in pausedUP / checkingUP / completed / moving to remain
# stuck at "downloading" in AutoRrent's UI.
QBITTORRENT_COMPLETE_STATES = {
    "uploading",    # seeding normally
    "stalledUP",    # seeding, no peers
    "checkingUP",   # integrity check after completion
    "forcedUP",     # force-started seeding
    "pausedUP",     # paused after completion
    "completed",    # explicitly marked complete by qBittorrent
    "moving",       # being moved to final location — shown as complete in UI,
                    # but grace-period clock only starts once the move finishes.
}

DOWNLOADING_STATES = {"downloading", "stalledDL", "forcedDL", "checkingDL", "metaDL"}
ERROR_STATES = {"error", "missingFiles"}

# Seconds to wait after first seeing a complete state before removing the
# torrent from qBittorrent.  Hardcoded — not user-configurable.
_GRACE_PERIOD = timedelta(seconds=60)


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

        # Skip qBittorrent query for torrents we've already removed or
        # that are in a terminal error / legacy-done state.
        skip_qbit = (
            not d.torrent_hash
            or d.qbit_removed
            or d.status in ("error", "done")
        )

        if not skip_qbit:
            try:
                qs = get_torrent_status(d.torrent_hash)

                if qs is None:
                    # Torrent not found in qBittorrent — removed externally.
                    # If we're past the grace period mark it cleaned up.
                    if (d.status == "completed"
                            and d.completion_first_seen_at is not None
                            and datetime.utcnow() - d.completion_first_seen_at >= _GRACE_PERIOD):
                        d.qbit_removed = True
                        logger.info(
                            "Torrent '%s' (%s) absent from qBittorrent — marking removed.",
                            d.title, d.torrent_hash,
                        )
                        db.commit()
                else:
                    item["progress"] = qs.get("progress")
                    item["eta"] = qs.get("eta")
                    item["dlspeed"] = qs.get("dlspeed")
                    if d.size_bytes is None and qs.get("size"):
                        d.size_bytes = qs["size"]

                    qstate = qs.get("status", "")

                    if qstate in QBITTORRENT_COMPLETE_STATES:
                        # Mark completed on first detection.
                        if d.status != "completed":
                            d.status = "completed"
                            item["status"] = "completed"
                            _update_history_completed(db, d.torrent_hash, d.size_bytes)

                        # Start grace-period clock once out of "moving".
                        if d.completion_first_seen_at is None and qstate != "moving":
                            d.completion_first_seen_at = datetime.utcnow()

                        db.commit()

                        # After grace period: fire hooks then remove from qBittorrent.
                        if (d.completion_first_seen_at is not None
                                and not d.qbit_removed
                                and datetime.utcnow() - d.completion_first_seen_at >= _GRACE_PERIOD):

                            # 1. Plex / Jellyfin library refresh
                            settings_dict = {s.key: s.value for s in db.query(Setting).all()}
                            _fire_media_refresh(settings_dict)

                            # 2. Remove from qBittorrent (files are always kept)
                            try:
                                remove_torrent(d.torrent_hash, delete_files=False)
                                d.qbit_removed = True
                                db.commit()
                                logger.info(
                                    "Removed completed torrent '%s' (%s) from qBittorrent"
                                    " after grace period.",
                                    d.title, d.torrent_hash,
                                )
                            except Exception as del_err:
                                logger.warning(
                                    "Failed to remove torrent '%s' (%s) from qBittorrent"
                                    " — will retry: %s",
                                    d.title, d.torrent_hash, del_err,
                                )

                    elif qstate in DOWNLOADING_STATES:
                        d.status = "downloading"
                        item["status"] = "downloading"
                        db.commit()

                    elif qstate in ERROR_STATES:
                        prev_status = d.status
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
