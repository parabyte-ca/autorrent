import logging
import os
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

_START_TIME = time.time()


@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning("Health check: DB unreachable — %s", e)
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "uptime_seconds": int(time.time() - _START_TIME),
        "version": os.environ.get("APP_VERSION", "dev"),
    }
