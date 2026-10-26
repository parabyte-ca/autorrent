import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import updater

logger = logging.getLogger(__name__)
router = APIRouter()

# Authentication is enforced app-wide by AuthMiddleware (Cloudflare requests
# need a valid session token when a UI password is set; local-network requests
# are trusted, as for every other endpoint) — no per-route check is needed.


@router.get("/admin/updates")
async def get_update_status(db: Session = Depends(get_db)):
    return updater.get_status(db, include_log=True)


@router.post("/admin/updates/check")
async def check_now(db: Session = Depends(get_db)):
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
    version = body.get("version", "")
    if not version:
        raise HTTPException(status_code=422, detail="version is required.")

    # Identify who is requesting (for the audit record)
    requested_by = (request.headers.get("X-Forwarded-For") or "authenticated user").split(",")[0].strip()[:200]

    try:
        updater.request_upgrade(db, version, requested_by, rollback=body.get("rollback") is True)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return updater.get_status(db)
