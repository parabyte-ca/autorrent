"""Episode lists from TVMaze, cached in-process.

Powers season-pack decisions, the quality-profile fallback timer and (later)
the episode guide and calendar.  Failures are never cached, and "unknown"
(None) is always distinguishable from "known and empty".
"""
import asyncio
import threading
import time
from datetime import datetime, timezone

from . import tvmaze

_TTL_SECONDS = 6 * 3600
_cache: dict[int, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _parse_dt(stamp: str | None, airdate: str | None) -> datetime | None:
    try:
        if stamp:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if airdate:
            return datetime.fromisoformat(airdate).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


def _normalise(raw: list[dict]) -> list[dict]:
    out = []
    for e in raw:
        number = e.get("number")
        season = e.get("season")
        if number is None or season is None or e.get("type", "regular") != "regular":
            continue  # specials and unnumbered entries never count toward a season
        out.append({
            "season": int(season),
            "number": int(number),
            "name": e.get("name") or "",
            "airs_at": _parse_dt(e.get("airstamp"), e.get("airdate")),
        })
    return out


def clear_cache() -> None:
    with _lock:
        _cache.clear()


async def aget_episodes(tvmaze_id: int | None) -> list[dict] | None:
    """Normalised regular episodes, or None if unknown/unreachable."""
    if not tvmaze_id:
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(tvmaze_id)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    raw = await tvmaze.get_episodes(tvmaze_id)
    if raw is None:
        return None
    episodes = _normalise(raw)
    with _lock:
        _cache[tvmaze_id] = (now, episodes)
    return episodes


def get_episodes(tvmaze_id: int | None) -> list[dict] | None:
    """Sync wrapper for scheduler threads (which have no running event loop)."""
    return asyncio.run(aget_episodes(tvmaze_id))


def season_episodes(episodes: list[dict] | None, season: int) -> list[dict]:
    return sorted((e for e in episodes or [] if e["season"] == season), key=lambda e: e["number"])


def season_complete(tvmaze_id: int | None, season: int, now: datetime | None = None) -> bool | None:
    """True if every episode of the season has aired, False if not, None if unknown."""
    episodes = get_episodes(tvmaze_id)
    eps = season_episodes(episodes, season)
    if not eps:
        return None
    now = now or datetime.now(timezone.utc)
    return all(e["airs_at"] is not None and e["airs_at"] <= now for e in eps)


def aired_episode_numbers(tvmaze_id: int | None, season: int, now: datetime | None = None) -> list[int]:
    """Numbers of the season's episodes that have aired (empty if unknown)."""
    now = now or datetime.now(timezone.utc)
    return [
        e["number"] for e in season_episodes(get_episodes(tvmaze_id), season)
        if e["airs_at"] is not None and e["airs_at"] <= now
    ]


def episode_age_hours(tvmaze_id: int | None, season: int, episode: int,
                      now: datetime | None = None) -> float | None:
    """Hours since the episode aired (negative if it hasn't), None if unknown."""
    for e in season_episodes(get_episodes(tvmaze_id), season):
        if e["number"] == episode and e["airs_at"] is not None:
            return ((now or datetime.now(timezone.utc)) - e["airs_at"]).total_seconds() / 3600
    return None
