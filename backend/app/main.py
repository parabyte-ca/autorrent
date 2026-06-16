import os
import pathlib
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import text

from .database import Base, SessionLocal, engine
from .middleware.auth import AuthMiddleware
from .models import Setting
from .routers import auth, backup, downloads, health, history, paths, search, settings, watchlist
from .services.scheduler import start_scheduler, stop_scheduler

STATIC_DIR = pathlib.Path("/app/static")
DATA_DIR = pathlib.Path("/app/data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # Migrate: add codec column if it doesn't exist yet (safe no-op if already present)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE watchlist ADD COLUMN codec TEXT DEFAULT 'x265'",
            "ALTER TABLE watchlist ADD COLUMN show_status TEXT",
            "ALTER TABLE watchlist ADD COLUMN show_status_checked_at DATETIME",
            "ALTER TABLE watchlist ADD COLUMN tvmaze_id INTEGER",
            "ALTER TABLE watchlist ADD COLUMN show_status_override INTEGER DEFAULT 0",
            "ALTER TABLE downloads ADD COLUMN completion_first_seen_at DATETIME",
            "ALTER TABLE downloads ADD COLUMN qbit_removed INTEGER DEFAULT 0",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass

    # Seed session_secret on first run (single-threaded here — no race)
    db = SessionLocal()
    try:
        if not db.query(Setting).filter(Setting.key == "session_secret").first():
            db.add(Setting(key="session_secret", value=secrets.token_hex(32)))
            db.commit()
    finally:
        db.close()

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AutoRrent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# AuthMiddleware is added after CORSMiddleware so CORS pre-flights are exempt
app.add_middleware(AuthMiddleware)

app.include_router(health.router)  # no auth, must be registered first
app.include_router(auth.router, prefix="/api")
app.include_router(backup.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(paths.router, prefix="/api")

# Serve compiled React frontend (production)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    # SW and manifest must not be cached by the browser so updates apply promptly
    _NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    _NO_CACHE_FILES = {"sw.js", "manifest.webmanifest"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if candidate.exists() and candidate.is_file():
            headers = _NO_CACHE_HEADERS if full_path in _NO_CACHE_FILES else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(STATIC_DIR / "index.html")
