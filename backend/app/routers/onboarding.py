from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Download, DownloadPath, Setting, WatchlistItem

router = APIRouter()

_DONE_KEY = "onboarding_done"


@router.get("/onboarding/status")
def onboarding_status(db: Session = Depends(get_db)):
    """Whether to show the first-run wizard.

    Never for an install that is already in use — a watchlist, downloads, a
    saved qBittorrent password or download folders all mean it has been set up.
    """
    def setting(key: str) -> str:
        row = db.query(Setting).filter(Setting.key == key).first()
        return (row.value or "") if row else ""

    done = setting(_DONE_KEY).lower() == "true"
    in_use = bool(
        db.query(WatchlistItem).first()
        or db.query(Download).first()
        or db.query(DownloadPath).first()
        or setting("qbit_password")
    )
    return {"needed": not done and not in_use}


@router.post("/onboarding/complete")
def onboarding_complete(db: Session = Depends(get_db)):
    """Mark the wizard finished or skipped so it is not shown again."""
    row = db.query(Setting).filter(Setting.key == _DONE_KEY).first()
    if row:
        row.value = "true"
    else:
        db.add(Setting(key=_DONE_KEY, value="true"))
    db.commit()
    return {"ok": True}
