import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting
from ..services.token import make_token

router = APIRouter(tags=["auth"])

# In-memory rate limiter: ip -> list of attempt timestamps
_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 300   # 5 minutes
_RATE_LIMIT = 5


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    cutoff = now - _RATE_WINDOW
    recent = [t for t in _ATTEMPTS[ip] if t > cutoff]
    _ATTEMPTS[ip] = recent
    if len(recent) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 5 minutes.")
    _ATTEMPTS[ip].append(now)


def _get_or_create_secret(db: Session) -> str:
    row = db.query(Setting).filter(Setting.key == "session_secret").first()
    if row and row.value:
        return row.value
    secret = secrets.token_hex(32)
    try:
        db.add(Setting(key="session_secret", value=secret))
        db.commit()
        return secret
    except Exception:
        db.rollback()
        row = db.query(Setting).filter(Setting.key == "session_secret").first()
        return row.value if row else secret


class LoginRequest(BaseModel):
    password: str


@router.get("/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    via_cf = "cf-connecting-ip" in request.headers
    row = db.query(Setting).filter(Setting.key == "ui_password").first()
    password_set = bool(row and row.value)
    return {"auth_required": password_set and via_cf}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    _check_rate_limit(client_ip)

    row = db.query(Setting).filter(Setting.key == "ui_password").first()
    stored = row.value if row else ""

    if not stored:
        raise HTTPException(status_code=400, detail="Authentication is not configured.")

    if not secrets.compare_digest(body.password, stored):
        raise HTTPException(status_code=401, detail="Incorrect password.")

    secret = _get_or_create_secret(db)
    return {"token": make_token(secret, stored)}


@router.post("/auth/revoke")
def revoke_sessions(db: Session = Depends(get_db)):
    """Delete session_secret so all existing tokens become invalid on next login."""
    db.query(Setting).filter(Setting.key == "session_secret").delete()
    db.commit()
    return {"ok": True}
