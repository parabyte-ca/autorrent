import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import text

from .database import Base, engine
from .routers import downloads, paths, search, settings, watchlist
from .services.scheduler import start_scheduler, stop_scheduler

STATIC_DIR = pathlib.Path("/app/static")
DATA_DIR = pathlib.Path("/app/data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # Migrate: add codec column if it doesn't exist yet (safe no-op if already present)
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE watchlist ADD COLUMN codec TEXT DEFAULT 'x265'"))
            conn.commit()
        except Exception:
            pass
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AutoRrent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(paths.router, prefix="/api")

# Serve compiled React frontend (production)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
