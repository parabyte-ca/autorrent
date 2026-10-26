import asyncio
import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import QualityProfile, WatchlistEpisode, WatchlistItem
from ..schemas import (
    AddFromTvmazeRequest,
    MarkEpisodeRequest,
    WatchlistCreate,
    WatchlistEpisodeOut,
    WatchlistOut,
    WatchlistUpdate,
)

from ..services import show_metadata, watchlist_add
from ..services.calendar_view import build_guide
from ..services.grab import grab_episode

logger = logging.getLogger(__name__)
router = APIRouter()

_ENRICH_TIMEOUT = 12  # seconds; metadata is cosmetic and must not stall adding a show
_REFRESH_BATCH = 25
_REFRESH_PACING = 0.6  # seconds between shows; TVMaze allows ~20 calls per 10 s


def _require_profile(db: Session, profile_id: int | None) -> None:
    if profile_id is not None and not db.query(QualityProfile).filter(QualityProfile.id == profile_id).first():
        raise HTTPException(status_code=422, detail="Quality profile not found")


@router.get("/watchlist", response_model=list[WatchlistOut])
def get_watchlist(db: Session = Depends(get_db)):
    return db.query(WatchlistItem).order_by(WatchlistItem.created_at.desc()).all()


@router.post("/watchlist", response_model=WatchlistOut, status_code=201)
async def create_watchlist_item(item: WatchlistCreate, db: Session = Depends(get_db)):
    _require_profile(db, item.profile_id)
    db_item = WatchlistItem(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    try:
        await asyncio.wait_for(show_metadata.enrich_item(db_item, show_metadata.tmdb_key(db)), _ENRICH_TIMEOUT)
        db.commit()
        db.refresh(db_item)
    except Exception as exc:  # timeout or lookup failure: the show is already saved
        db.rollback()
        logger.warning("Metadata enrichment skipped for %r: %s", db_item.title, exc)
    return db_item


@router.post("/watchlist/from-tvmaze", response_model=WatchlistOut, status_code=201)
async def add_from_tvmaze(body: AddFromTvmazeRequest, db: Session = Depends(get_db)):
    """Add a show picked from Browse, starting at its next new episode or the beginning."""
    if body.profile_id is not None:
        _require_profile(db, body.profile_id)  # an explicit choice must exist
    try:
        return await watchlist_add.create_from_tvmaze(
            db, body.tvmaze_id, start=body.start, profile_id=body.profile_id,
        )
    except watchlist_add.AlreadyOnWatchlist:
        raise HTTPException(status_code=409, detail="That show is already on your watchlist.")
    except watchlist_add.ShowNotFound:
        raise HTTPException(status_code=404, detail="TVMaze could not find that show.")


@router.post("/watchlist/refresh-metadata")
async def refresh_metadata(db: Session = Depends(get_db)):
    """Find posters/blurbs for shows that have none and are due a lookup.

    Paced to stay inside TVMaze's rate limit, and stops at the first sign it is
    unavailable instead of hammering it; those shows stay due for the next call.
    """
    now = datetime.now(timezone.utc)
    due = [
        i for i in db.query(WatchlistItem).filter(WatchlistItem.poster_url.is_(None)).all()
        if show_metadata.metadata_due(i, now)
    ]
    checked = matched = 0
    unavailable = False
    key = show_metadata.tmdb_key(db)
    for n, item in enumerate(due[:_REFRESH_BATCH]):
        if n:
            await asyncio.sleep(_REFRESH_PACING)
        result = await show_metadata.enrich_item(item, key)
        if result == show_metadata.UNAVAILABLE:
            unavailable = True
            break
        checked += 1
        matched += result == show_metadata.FOUND
        db.commit()
    return {"checked": checked, "matched": matched, "remaining": len(due) - checked,
            "unavailable": unavailable}


@router.put("/watchlist/{item_id}", response_model=WatchlistOut)
def update_watchlist_item(
    item_id: int, item: WatchlistUpdate, db: Session = Depends(get_db)
):
    db_item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    _require_profile(db, item.profile_id)
    for field, value in item.model_dump(exclude_unset=True).items():
        setattr(db_item, field, value)
    db.commit()
    db.refresh(db_item)
    return db_item


@router.delete("/watchlist/{item_id}")
def delete_watchlist_item(item_id: int, db: Session = Depends(get_db)):
    db_item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(db_item)
    db.commit()
    return {"ok": True}


@router.post("/watchlist/check-show-statuses")
async def trigger_show_status_check(db: Session = Depends(get_db)):
    """Immediately check TVMaze status for all active watchlist items."""
    from ..services.scheduler import _check_statuses_async
    await _check_statuses_async(db)
    return {"ok": True, "message": "Show status check complete"}


@router.get("/watchlist/{item_id}/guide")
async def get_episode_guide(item_id: int, db: Session = Depends(get_db)):
    """Every known episode of the show: downloaded, missing, or still to air."""
    item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.tvmaze_id and not item.poster_url:
        # Shows added before TVMaze matching existed: try once now.
        # (A poster without a TVMaze id means a movie matched on TMDB: no guide.)
        await show_metadata.enrich_item(item, show_metadata.tmdb_key(db))
        db.commit()
    return await build_guide(db, item)


@router.post("/watchlist/{item_id}/episodes/{season}/{episode}/search")
def search_single_episode(item_id: int, season: int, episode: int, db: Session = Depends(get_db)):
    """Search for one specific episode right now and start downloading it."""
    item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if season < 0 or episode < 1:
        raise HTTPException(status_code=422, detail="Invalid season or episode")
    try:
        return grab_episode(db, item, season, episode)
    except Exception as exc:
        logger.error("Manual episode grab failed for %r S%02dE%02d: %s", item.title, season, episode, exc)
        raise HTTPException(status_code=502, detail=f"Could not start the download: {exc}")


@router.post("/watchlist/{item_id}/scan")
def scan_single_item(item_id: int, db: Session = Depends(get_db)):
    db_item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    from ..services.scheduler import scan_watchlist
    threading.Thread(target=lambda: scan_watchlist(target_item_id=item_id), daemon=True).start()
    return {"ok": True, "message": "Scan triggered"}


@router.post("/scan")
def trigger_full_scan():
    from ..services.scheduler import scan_watchlist
    threading.Thread(target=scan_watchlist, daemon=True).start()
    return {"ok": True, "message": "Full scan triggered"}


# ── Episode tracking ──────────────────────────────────────────────────────────


@router.get("/watchlist/{item_id}/episodes", response_model=list[WatchlistEpisodeOut])
def get_episodes(item_id: int, db: Session = Depends(get_db)):
    """Return all episode records for a watchlist item, ordered season/episode asc."""
    if not db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first():
        raise HTTPException(status_code=404, detail="Item not found")
    return (
        db.query(WatchlistEpisode)
        .filter(WatchlistEpisode.watchlist_id == item_id)
        .order_by(WatchlistEpisode.season, WatchlistEpisode.episode)
        .all()
    )


@router.post(
    "/watchlist/{item_id}/episodes",
    response_model=WatchlistEpisodeOut,
    status_code=201,
)
def mark_episode(
    item_id: int, payload: MarkEpisodeRequest, db: Session = Depends(get_db)
):
    """Manually mark an episode as downloaded.

    Returns 409 if a record for that season/episode already exists.
    """
    if not db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first():
        raise HTTPException(status_code=404, detail="Item not found")
    ep = WatchlistEpisode(
        watchlist_id=item_id,
        season=payload.season,
        episode=payload.episode,
        torrent_name=payload.torrent_name,
        downloaded_at=datetime.now(timezone.utc),
    )
    db.add(ep)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Episode already marked as downloaded",
        )
    db.refresh(ep)
    return ep


@router.delete("/watchlist/{item_id}/episodes", status_code=200)
def reset_episodes(item_id: int, confirm: str = "", db: Session = Depends(get_db)):
    """Delete all episode records for a watchlist item.

    Requires ``?confirm=true`` to prevent accidental wipes.
    """
    if not db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first():
        raise HTTPException(status_code=404, detail="Item not found")
    if confirm != "true":
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to delete all episode records.",
        )
    count = (
        db.query(WatchlistEpisode)
        .filter(WatchlistEpisode.watchlist_id == item_id)
        .count()
    )
    db.query(WatchlistEpisode).filter(WatchlistEpisode.watchlist_id == item_id).delete()
    db.commit()
    return {"deleted": count}


@router.delete("/watchlist/{item_id}/episodes/{episode_id}", status_code=204)
def delete_episode(item_id: int, episode_id: int, db: Session = Depends(get_db)):
    """Delete a single episode record by ID."""
    ep = (
        db.query(WatchlistEpisode)
        .filter(
            WatchlistEpisode.id == episode_id,
            WatchlistEpisode.watchlist_id == item_id,
        )
        .first()
    )
    if not ep:
        raise HTTPException(status_code=404, detail="Episode record not found")
    db.delete(ep)
    db.commit()
    return Response(status_code=204)


# ── Show-status override ──────────────────────────────────────────────────────


@router.post("/watchlist/{item_id}/override-status", response_model=WatchlistOut)
def set_status_override(item_id: int, db: Session = Depends(get_db)):
    """Mark item as override — auto-pause won't fire even if show has ended.

    Also re-enables the item so it resumes scanning immediately.
    """
    item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.show_status_override = True
    item.enabled = True
    db.commit()
    db.refresh(item)
    return item


@router.delete("/watchlist/{item_id}/override-status", response_model=WatchlistOut)
def remove_status_override(item_id: int, db: Session = Depends(get_db)):
    """Remove override — auto-pause will fire again on the next status check."""
    item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.show_status_override = False
    db.commit()
    db.refresh(item)
    return item
