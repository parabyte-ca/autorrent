import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import updater

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_auth(request: Request) -> None:
    """Raise 401 if the request lacks a valid Bearer token."""
    token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")


@router.get("/admin/updates")
async def get_update_status(request: Request, db: Session = Depends(get_db)):
    _require_auth(request)
    token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    # Log tail is only returned when a valid session is present (same auth level here)
    include_log = bool(token)
    return updater.get_status(db, include_log=include_log)


@router.post("/admin/updates/check")
async def check_now(request: Request, db: Session = Depends(get_db)):
    _require_auth(request)

    if not updater.check_now_rate_limited():
        # Return cached status rather than 429 so the UI still gets data
        return {"rate_limited": True, **updater.get_status(db)}

    try:
        await updater.fetch_and_cache(db)
    except Exception as exc:
        logger.error("Manual update check failed: %s", exc)

    return updater.get_status(db)


@router.post("/admin/updates/upgrade")
async def request_upgrade(request: Request, body: dict, db: Session = Depends(get_db)):
    _require_auth(request)

    version = body.get("version", "")
    if not version:
        raise HTTPException(status_code=422, detail="version is required.")

    # Identify who is requesting (for the audit record)
    requested_by = (request.headers.get("X-Forwarded-For") or "authenticated user").split(",")[0].strip()[:200]

    try:
        updater.request_upgrade(db, version, requested_by)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return updater.get_status(db)
