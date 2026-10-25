from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting, TraktSeen
from ..schemas import TraktTestRequest
from ..services import trakt, trakt_sync

router = APIRouter()


def _get(db: Session, key: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value or "") if row else ""


@router.get("/trakt/status")
def trakt_status(db: Session = Depends(get_db)):
    result = trakt_sync.last_result(db)
    return {
        "configured": bool(_get(db, "trakt_client_id").strip() and _get(db, "trakt_username").strip()),
        "enabled": _get(db, "trakt_enabled").lower() == "true",
        "last_result": result,
        "seen_count": db.query(TraktSeen).count(),
        "max_new_per_run": trakt_sync.MAX_NEW_PER_RUN,
    }


@router.post("/trakt/test")
async def test_trakt(body: TraktTestRequest):
    """Check the client ID and username against Trakt using the form's current values."""
    if not body.client_id.strip() or not body.username.strip():
        return {"ok": False, "error": "Enter both the client ID and your Trakt username first."}
    try:
        shows = await trakt.fetch_watchlist_shows(body.client_id.strip(), body.username.strip())
    except trakt.TraktError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "shows": len(shows)}


@router.post("/trakt/sync")
async def sync_now(db: Session = Depends(get_db)):
    """Sync the Trakt watchlist right now (works even if the schedule is switched off)."""
    if not (_get(db, "trakt_client_id").strip() and _get(db, "trakt_username").strip()):
        raise HTTPException(status_code=400, detail="Enter your Trakt client ID and username in Settings first.")
    return await trakt_sync.sync(db)


@router.delete("/trakt/seen")
def forget_history(db: Session = Depends(get_db)):
    """Forget which Trakt shows were handled, so the next sync considers all of them again."""
    count = db.query(TraktSeen).delete()
    db.commit()
    return {"ok": True, "cleared": count}
