"""
history.py — Download history log.

Endpoints
─────────
  GET  /api/history          Paginated, filterable list of all history records.
  GET  /api/history/export   Full history as a CSV file download.
  DELETE /api/history/{id}   Remove a single record.
  DELETE /api/history        Remove all records (requires ?confirm=true).

History records are created by downloads.py (manual) and scheduler.py
(watchlist auto-downloads).  Deleting a record here does not affect the
underlying torrent in qBittorrent.
"""
import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DownloadHistory

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fmt_size(b: Optional[int]) -> str:
    """Return a human-readable file size string, e.g. '4.2 GB'."""
    if b is None:
        return "—"
    for unit, divisor in [("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)]:
        if b >= divisor:
            return f"{b / divisor:.1f} {unit}"
    return f"{b} B"


def _serialize(h: DownloadHistory) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "source": h.source,
        "indexer": h.indexer,
        "folder": h.folder,
        "torrent_hash": h.torrent_hash,
        "size_bytes": h.size_bytes,
        "size_human": _fmt_size(h.size_bytes),
        # Always append 'Z' so clients know these are UTC timestamps.
        "added_at": h.added_at.isoformat() + "Z" if h.added_at else None,
        "completed_at": h.completed_at.isoformat() + "Z" if h.completed_at else None,
        "status": h.status,
        "watchlist_id": h.watchlist_id,
        "error_msg": h.error_msg,
    }


def _build_query(db: Session, q: str, source: str, status: str):
    """Return a filtered SQLAlchemy query over DownloadHistory."""
    query = db.query(DownloadHistory)
    if q:
        query = query.filter(DownloadHistory.name.ilike(f"%{q}%"))
    if source != "all":
        query = query.filter(DownloadHistory.source == source)
    if status != "all":
        query = query.filter(DownloadHistory.status == status)
    return query


# ── Routes ────────────────────────────────────────────────────────────────────


# /history/export must be defined before /history/{id} so FastAPI does not
# attempt to cast the literal string "export" to an integer path parameter.
@router.get("/history/export")
def export_history_csv(db: Session = Depends(get_db)):
    """Download all history rows as a CSV file."""
    items = db.query(DownloadHistory).order_by(DownloadHistory.added_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "name", "source", "indexer", "folder", "torrent_hash",
        "size_bytes", "size_human", "added_at", "completed_at",
        "status", "watchlist_id", "error_msg",
    ])
    for h in items:
        row = _serialize(h)
        writer.writerow([
            row["id"],
            row["name"],
            row["source"],
            row["indexer"] or "",
            row["folder"] or "",
            row["torrent_hash"] or "",
            row["size_bytes"] if row["size_bytes"] is not None else "",
            row["size_human"],
            row["added_at"] or "",
            row["completed_at"] or "",
            row["status"],
            row["watchlist_id"] if row["watchlist_id"] is not None else "",
            row["error_msg"] or "",
        ])

    buf.seek(0)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"autorrent-history-{today}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/history")
def list_history(
    q: str = "",
    source: str = "all",
    status: str = "all",
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Return a paginated, filtered list of history records.

    ``total`` is the count matching the current filters (before pagination),
    which the frontend uses to drive the pagination controls.
    """
    limit = min(limit, 200)
    query = _build_query(db, q, source, status)
    total = query.count()
    items = query.order_by(DownloadHistory.added_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_serialize(h) for h in items], "total": total}


@router.delete("/history/{history_id}")
def delete_history_item(history_id: int, db: Session = Depends(get_db)):
    """Delete a single history record by ID."""
    h = db.query(DownloadHistory).filter(DownloadHistory.id == history_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="History record not found.")
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.delete("/history")
def clear_history(confirm: str = "", db: Session = Depends(get_db)):
    """Delete all history records.  Requires ``?confirm=true`` to prevent accidents."""
    if confirm != "true":
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to delete all history records.",
        )
    count = db.query(DownloadHistory).count()
    db.query(DownloadHistory).delete()
    db.commit()
    return {"deleted": count}
