import logging
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Lowercase, strip non-alphanumeric, collapse spaces."""
    lowered = name.lower()
    cleaned = re.sub(r"[^a-z0-9 ]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def check_duplicate(
    torrent_hash: str | None,
    torrent_name: str,
    db: Session,
) -> dict:
    """Check whether a torrent appears to have already been downloaded.

    Checks, in order:
      1. Exact hash match in DownloadHistory
      2. Normalised name match in the 500 most recent DownloadHistory rows
      3. Active torrent hash match in qBittorrent (best-effort, skipped on error)

    Always returns a dict and never raises.
    """
    try:
        from ..models import DownloadHistory

        _not_found = {
            "is_duplicate": False,
            "match_type": None,
            "matched_name": None,
            "matched_at": None,
        }

        def _iso(dt) -> str | None:
            return (dt.isoformat() + "Z") if dt else None

        # 1. Exact hash match
        if torrent_hash and torrent_hash.strip():
            normalized_hash = torrent_hash.strip().lower()
            hist = (
                db.query(DownloadHistory)
                .filter(func.lower(DownloadHistory.torrent_hash) == normalized_hash)
                .first()
            )
            if hist:
                return {
                    "is_duplicate": True,
                    "match_type": "hash",
                    "matched_name": hist.name,
                    "matched_at": _iso(hist.added_at),
                }

        # 2. Normalised name match (recent 500 rows to bound CPU cost)
        norm_query = normalize_name(torrent_name)
        recent = (
            db.query(DownloadHistory)
            .order_by(DownloadHistory.added_at.desc())
            .limit(500)
            .all()
        )
        for row in recent:
            if normalize_name(row.name) == norm_query:
                return {
                    "is_duplicate": True,
                    "match_type": "name",
                    "matched_name": row.name,
                    "matched_at": _iso(row.added_at),
                }

        # 3. Active qBittorrent torrent check (hash only, best-effort)
        if torrent_hash and torrent_hash.strip():
            try:
                from .qbittorrent import _get_client
                client = _get_client()
                active = client.torrents_info(torrent_hashes=torrent_hash.strip())
                if active:
                    return {
                        "is_duplicate": True,
                        "match_type": "active",
                        "matched_name": active[0].name,
                        "matched_at": None,
                    }
            except Exception as qbit_err:
                logger.warning("qBittorrent active-torrent check skipped: %s", qbit_err)

        return _not_found

    except Exception as exc:
        logger.error("Duplicate check failed unexpectedly: %s", exc)
        return {
            "is_duplicate": False,
            "match_type": None,
            "matched_name": None,
            "matched_at": None,
        }
