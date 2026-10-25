"""Discover shows: what is airing soon (from TVMaze schedules) and free-text search."""
import asyncio
import re
import time
from datetime import date, datetime, timedelta, timezone

from . import show_metadata, tvmaze

_TTL_SECONDS = 6 * 3600
_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

# Daily/panel formats flood the schedule and are not what a torrent watchlist is for.
_SKIP_TYPES = {"News", "Talk Show", "Sports", "Variety", "Game Show", "Panel Show", "Award Show"}
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")


def clear_cache() -> None:
    _cache.clear()


def valid_country(code: str | None) -> str:
    return code.upper() if code and _COUNTRY.match(code) else "US"


def show_card(show: dict) -> dict:
    return {
        "tvmaze_id": show.get("id"),
        "name": show.get("name") or "",
        "poster_url": show_metadata.poster_url(show),
        "network": show_metadata.network_name(show),
        "premiered": show.get("premiered"),
        "status": show.get("status"),
        "genres": show.get("genres") or [],
        "rating": (show.get("rating") or {}).get("average"),
        "summary": show_metadata.clean_summary(show.get("summary"), 260),
        "next_airs": None,
        "next_episode": None,
        "season_premiere": False,
    }


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    except ValueError:
        return None


def aggregate_schedule(episodes: list[dict], now: datetime) -> list[dict]:
    """Collapse schedule episodes into one card per show, most popular first."""
    cards: dict[int, dict] = {}
    weights: dict[int, int] = {}
    airings: dict[int, list[tuple[datetime, int | None, int | None]]] = {}
    for ep in episodes:
        show = (ep.get("_embedded") or {}).get("show")
        if not show or show.get("id") is None or show.get("type") in _SKIP_TYPES:
            continue
        sid = show["id"]
        cards.setdefault(sid, show_card(show))
        weights[sid] = show.get("weight") or 0
        if ep.get("number") == 1:
            cards[sid]["season_premiere"] = True
        at = _parse(ep.get("airstamp"))
        if at is not None:
            airings.setdefault(sid, []).append((at, ep.get("season"), ep.get("number")))

    # Show the next airing (allowing a few hours' grace), else the earliest we know of.
    for sid, times in airings.items():
        times.sort(key=lambda t: t[0])
        upcoming = [t for t in times if t[0] >= now - timedelta(hours=6)]
        at, season, number = (upcoming or times)[0]
        cards[sid]["next_airs"] = at.isoformat().replace("+00:00", "Z")
        if season is not None and number is not None:
            cards[sid]["next_episode"] = f"S{season:02d}E{number:02d}"

    return sorted(cards.values(), key=lambda c: (-weights[c["tvmaze_id"]], c["name"].lower()))


async def airing_shows(country: str, days: int = 7, today: date | None = None,
                       limit: int = 80) -> list[dict] | None:
    """Shows airing in the next ``days`` days in ``country``; None if TVMaze was unreachable."""
    country = valid_country(country)
    today = today or datetime.now(timezone.utc).date()
    key = (country, today.isoformat())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1][:limit]

    sem = asyncio.Semaphore(4)

    async def fetch(day: date, web: bool):
        async with sem:
            return await tvmaze.get_schedule(day.isoformat(), country, web=web)

    days_list = [today + timedelta(days=i) for i in range(days)]
    results = await asyncio.gather(*(fetch(d, w) for d in days_list for w in (False, True)))
    if all(r is None for r in results):
        return None

    episodes = [e for r in results if r for e in r]
    cards = aggregate_schedule(episodes, datetime.now(timezone.utc))
    _cache[key] = (time.monotonic(), cards)
    return cards[:limit]


async def search(query: str, limit: int = 30) -> list[dict] | None:
    shows = await tvmaze.search_shows(query)
    if shows is None:
        return None
    return [show_card(s) for s in shows[:limit]]
