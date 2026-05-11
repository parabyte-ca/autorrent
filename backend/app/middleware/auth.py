import hashlib
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..database import SessionLocal
from ..models import Setting

_EXEMPT = {"/health", "/api/auth/login", "/api/auth/status"}


def _make_token(secret: str, password: str) -> str:
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # OPTIONS (CORS pre-flights) and non-API paths (static files, SPA) always pass through
        if request.method == "OPTIONS" or not request.url.path.startswith("/api/"):
            return await call_next(request)

        # Specific exempt API paths (login, status)
        if request.url.path in _EXEMPT:
            return await call_next(request)

        # No CF-Connecting-IP header → local network access → always allowed
        if "cf-connecting-ip" not in request.headers:
            return await call_next(request)

        # CF request: load password + secret
        db = SessionLocal()
        try:
            rows = {
                r.key: r.value
                for r in db.query(Setting).filter(
                    Setting.key.in_(["ui_password", "session_secret"])
                ).all()
            }
        finally:
            db.close()

        password = rows.get("ui_password", "")
        if not password:
            # Password not configured → CF requests also pass through
            return await call_next(request)

        # Require valid Bearer token
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"detail": "Authentication required."}, status_code=401)

        candidate = auth_header[len("Bearer "):].strip()
        secret = rows.get("session_secret", "")
        if not secret:
            return JSONResponse({"detail": "Auth not initialised. Please log in."}, status_code=401)

        expected = _make_token(secret, password)
        if not hmac.compare_digest(expected, candidate):
            return JSONResponse({"detail": "Invalid or expired token."}, status_code=401)

        return await call_next(request)
