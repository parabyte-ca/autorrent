"""Episode guide (per show) and upcoming-episodes calendar, built from TVMaze + our records."""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import episode_guide


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _status(downloaded: bool, airs_at: datetime | None, now: datetime) -> str:
    if downloaded:
        return "downloaded"
    if airs_at is not None and airs_at <= now:
        return "missing"
    return "upcoming"


async def build_guide(db, item, now: datetime | None = None) -> dict:
    """Every known episode of a show with whether we have it, miss it, or await it."""
    from ..models import WatchlistEpisode

    now = now or datetime.now(timezone.utc)
    episodes = await episode_guide.aget_episodes(item.tvmaze_id)
    if episodes is None:
        return {"available": False, "seasons": [],
                "pointer": {"season": item.season, "episode": item.episode}}

    have = {
        (e.season, e.episode): e
        for e in db.query(WatchlistEpisode).filter_by(watchlist_id=item.id)
    }
    seasons: dict[int, list[dict]] = defaultdict(list)
    for e in sorted(episodes, key=lambda e: (e["season"], e["number"])):
        rec = have.get((e["season"], e["number"]))
        seasons[e["season"]].append({
            "number": e["number"],
            "name": e["name"],
            "airs_at": _iso(e["airs_at"]),
            "status": _status(rec is not None, e["airs_at"], now),
            "torrent_name": rec.torrent_name if rec else None,
        })

    return {
        "available": True,
        "pointer": {"season": item.season, "episode": item.episode},
        "seasons": [
            {
                "season": s,
                "episodes": eps,
                "total": len(eps),
                "aired": sum(1 for e in eps if e["status"] != "upcoming"),
                "downloaded": sum(1 for e in eps if e["status"] == "downloaded"),
            }
            for s, eps in sorted(seasons.items())
        ],
    }


async def build_calendar(db, now: datetime | None = None, days_back: int = 7,
                         days_ahead: int = 30) -> dict:
    """Episodes of watchlisted shows airing in [now - days_back, now + days_ahead]."""
    from ..models import WatchlistEpisode, WatchlistItem

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_ahead)

    items = db.query(WatchlistItem).all()
    have: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for e in db.query(WatchlistEpisode).all():
        have[e.watchlist_id].add((e.season, e.episode))

    sem = asyncio.Semaphore(4)  # stay well inside TVMaze's rate limit

    async def load(item):
        async with sem:
            return item, await episode_guide.aget_episodes(item.tvmaze_id)

    loaded = await asyncio.gather(*(load(i) for i in items if i.tvmaze_id))

    entries = []
    unmatched = sum(1 for i in items if not i.tvmaze_id)
    for item, episodes in loaded:
        if episodes is None:
            unmatched += 1
            continue
        for e in episodes:
            at = e["airs_at"]
            if at is None or not (start <= at <= end):
                continue
            entries.append({
                "watchlist_id": item.id,
                "title": item.title,
                "poster_url": item.poster_url,
                "enabled": bool(item.enabled),
                "season": e["season"],
                "episode": e["number"],
                "name": e["name"],
                "airs_at": _iso(at),
                "status": _status((e["season"], e["number"]) in have[item.id], at, now),
            })

    entries.sort(key=lambda x: (x["airs_at"], x["title"].lower(), x["season"], x["episode"]))
    return {"entries": entries, "unmatched": unmatched}
