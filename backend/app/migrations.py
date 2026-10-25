"""Lightweight schema migrations for the SQLite database.

Each ALTER is a safe no-op if the column already exists.  An optional backfill
runs only when the ALTER actually added the column, so existing rows are not
treated as brand-new events.  Called on startup and again after a backup
restore (which swaps in a database file that may predate the current schema).
"""
from sqlalchemy import text

from . import models  # noqa: F401  (registers tables on Base.metadata)
from .database import Base, engine

MIGRATIONS: list[tuple[str, str | None]] = [
    ("ALTER TABLE watchlist ADD COLUMN codec TEXT DEFAULT 'x265'", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status_checked_at DATETIME", None),
    ("ALTER TABLE watchlist ADD COLUMN tvmaze_id INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN show_status_override INTEGER DEFAULT 0", None),
    ("ALTER TABLE downloads ADD COLUMN completion_first_seen_at DATETIME", None),
    ("ALTER TABLE downloads ADD COLUMN qbit_removed INTEGER DEFAULT 0", None),
    ("ALTER TABLE watchlist ADD COLUMN max_size_mb INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN preferred_groups TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN prefer_season_packs INTEGER DEFAULT 0", None),
    (
        "ALTER TABLE downloads ADD COLUMN notified_complete INTEGER DEFAULT 0",
        "UPDATE downloads SET notified_complete = 1 WHERE status IN ('completed', 'seeding', 'done')",
    ),
    (
        "ALTER TABLE downloads ADD COLUMN notified_failed INTEGER DEFAULT 0",
        "UPDATE downloads SET notified_failed = 1 WHERE status IN ('error', 'failed')",
    ),
    ("ALTER TABLE downloads ADD COLUMN notified_stalled INTEGER DEFAULT 0", None),
    ("ALTER TABLE downloads ADD COLUMN last_progress REAL", None),
    ("ALTER TABLE downloads ADD COLUMN last_progress_at DATETIME", None),
    ("ALTER TABLE downloads ADD COLUMN retry_count INTEGER DEFAULT 0", None),
    ("ALTER TABLE watchlist ADD COLUMN profile_id INTEGER", None),
    ("ALTER TABLE watchlist ADD COLUMN poster_url TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN summary TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN network TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN premiered TEXT", None),
    ("ALTER TABLE watchlist ADD COLUMN metadata_checked_at DATETIME", None),
    # Earlier versions stamped a show as "checked" even when TVMaze was merely
    # unreachable or rate-limited; clear those stamps once so they are retried.
    (
        "ALTER TABLE watchlist ADD COLUMN metadata_attempts INTEGER DEFAULT 0",
        "UPDATE watchlist SET metadata_checked_at = NULL WHERE poster_url IS NULL",
    ),
]


def run_migrations(bind=engine) -> None:
    Base.metadata.create_all(bind=bind)
    with bind.connect() as conn:
        for stmt, backfill in MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                continue
            if backfill:
                try:
                    conn.execute(text(backfill))
                    conn.commit()
                except Exception:
                    pass
