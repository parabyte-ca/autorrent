import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WatchlistItem
from ..schemas import WatchlistCreate, WatchlistOut, WatchlistUpdate

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
