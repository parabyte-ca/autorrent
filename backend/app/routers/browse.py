import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting, WatchlistItem
from ..services import browse as browse_service

router = APIRouter()


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@router.get("/browse")
async def browse(q: str = "", db: Session = Depends(get_db)):
    """Shows airing soon (default) or a TVMaze search, flagged if already on the watchlist."""
    row = db.query(Setting).filter(Setting.key == "browse_country").first()
    country = browse_service.valid_country(row.value if row else None)

    query = q.strip()
    if query:
        if len(query) < 2:
            return {"mode": "search", "country": country, "shows": []}
        shows = await browse_service.search(query)
        mode = "search"
    else:
        shows = await browse_service.airing_shows(country)
        mode = "airing"
    if shows is None:
        raise HTTPException(status_code=502, detail="Could not reach TVMaze. Try again in a moment.")

    items = db.query(WatchlistItem).all()
    by_id = {i.tvmaze_id: i.id for i in items if i.tvmaze_id}
    by_name = {_norm(i.title): i.id for i in items}
    for show in shows:
        wid = by_id.get(show["tvmaze_id"]) or by_name.get(_norm(show["name"]))
        show["watchlist_id"] = wid
        show["on_watchlist"] = wid is not None
    return {"mode": mode, "country": country, "shows": shows}
