from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.calendar_view import build_calendar

router = APIRouter()


@router.get("/calendar")
async def get_calendar(
    days: int = Query(30, ge=1, le=120, description="Days ahead to include"),
    back: int = Query(7, ge=0, le=30, description="Days back to include"),
    db: Session = Depends(get_db),
):
    """Upcoming (and just-aired) episodes of every watchlisted show."""
    return await build_calendar(db, days_back=back, days_ahead=days)
