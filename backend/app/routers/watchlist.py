import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WatchlistEpisode, WatchlistItem
from ..schemas import (
    MarkEpisodeRequest,
    WatchlistCreate,
    WatchlistEpisodeOut,
    WatchlistOut,
    WatchlistUpdate,
)

router = APIRouter()


@router.get("/watchlist", response_model=list[WatchlistOut])
def get_watchlist(db: Session = Depends(get_db)):
    return db.query(WatchlistItem).order_by(WatchlistItem.created_at.desc()).all()


@router.post("/watchlist", response_model=WatchlistOut, status_code=201)
def create_watchlist_item(item: WatchlistCreate, db: Session = Depends(get_db)):
    db_item = WatchlistItem(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@router.put("/watchlist/{item_id}", response_model=WatchlistOut)
def update_watchlist_item(
    item_id: int, item: WatchlistUpdate, db: Session = Depends(get_db)
):
    db_item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
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


@router.post("/watchlist/{item_id}/scan")
def scan_single_item(item_id: int, db: Session = Depends(get_db)):
    db_item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    from ..services.scheduler import scan_watchlist
    threading.Thread(target=scan_watchlist, daemon=True).start()
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
        downloaded_at=datetime.utcnow(),
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
