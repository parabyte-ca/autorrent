import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)
_BASE = "https://api.tvmaze.com"

_STATUS_MAP = {
    "Running": "Running",
    "Ended": "Ended",
    "To Be Determined": "To Be Determined",
    "In Development": "In Development",
}


class TVMazeUnavailable(Exception):
    """TVMaze could not answer (network error, rate limit, server error).

    Distinct from "no such show": callers must not remember this as a negative result.
    """


async def _get_json(client: httpx.AsyncClient, path: str, params: dict | None = None):
    """GET from TVMaze, politely retrying when rate limited (HTTP 429)."""
    for attempt in range(3):
        resp = await client.get(f"{_BASE}{path}", params=params)
        if resp.status_code == 429 and attempt < 2:
            try:
                delay = min(float(resp.headers.get("retry-after", "1")), 5.0)
            except ValueError:
                delay = 1.0
            await asyncio.sleep(delay)
            continue
        return resp
    return resp


async def _strict_get(path: str, params: dict | None = None, timeout: float = 10):
    """GET JSON from TVMaze. None means a genuine 404; raises TVMazeUnavailable on any failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await _get_json(client, path, params)
    except httpx.HTTPError as exc:
        raise TVMazeUnavailable(str(exc) or type(exc).__name__) from exc
    if resp.status_code == 404:
        return None
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TVMazeUnavailable(f"HTTP {resp.status_code}")
    try:
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TVMazeUnavailable(str(exc) or type(exc).__name__) from exc


async def lookup_by_name(title: str) -> dict | None:
    """Best fuzzy match for a title; None only if TVMaze has no match. Raises TVMazeUnavailable."""
    data = await _strict_get("/singlesearch/shows", {"q": title})
    return data if isinstance(data, dict) and data.get("id") else None


async def lookup_many(title: str) -> list[dict]:
    """All matches for a title, best first. Raises TVMazeUnavailable."""
    data = await _strict_get("/search/shows", {"q": title})
    if not isinstance(data, list):
        return []
    return [r["show"] for r in data if isinstance(r, dict) and r.get("show")]


async def fetch_show(tvmaze_id: int) -> dict | None:
    """A show by TVMaze id; None only if it does not exist. Raises TVMazeUnavailable."""
    data = await _strict_get(f"/shows/{tvmaze_id}")
    return data if isinstance(data, dict) and data.get("id") else None


async def search_show(title: str) -> dict | None:
    """Search TVMaze for the best-matching show by title.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        return await lookup_by_name(title)
    except TVMazeUnavailable as exc:
        logger.warning("TVMaze search failed for %r: %s", title, exc)
        return None


async def get_show(tvmaze_id: int) -> dict | None:
    """Fetch a show by its TVMaze ID.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        return await fetch_show(tvmaze_id)
    except TVMazeUnavailable as exc:
        logger.warning("TVMaze get_show failed for id=%d: %s", tvmaze_id, exc)
        return None


async def get_episodes(tvmaze_id: int) -> list[dict] | None:
    """Fetch every episode of a show (specials included) from TVMaze.

    Returns None on any network/API error so callers can tell "unknown" apart
    from "the show has no episodes".
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _get_json(client, f"/shows/{tvmaze_id}/episodes", {"specials": 1})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else None
    except Exception as exc:
        logger.warning("TVMaze get_episodes failed for id=%d: %s", tvmaze_id, exc)
        return None


async def lookup_show(imdb: str | None = None, thetvdb: int | str | None = None) -> dict | None:
    """Find a show by external id (IMDb or TheTVDB); None if unknown or on error."""
    if imdb:
        params = {"imdb": imdb}
    elif thetvdb:
        params = {"thetvdb": str(thetvdb)}
    else:
        return None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await _get_json(client, "/lookup/shows", params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) and data.get("id") else None
    except Exception as exc:
        logger.warning("TVMaze lookup failed for %s: %s", params, exc)
        return None


async def search_shows(query: str) -> list[dict] | None:
    """Free-text show search; returns show dicts best-match first, None on error."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await _get_json(client, "/search/shows", {"q": query})
            resp.raise_for_status()
            return [r["show"] for r in resp.json() if isinstance(r, dict) and r.get("show")]
    except Exception as exc:
        logger.warning("TVMaze search_shows failed for %r: %s", query, exc)
        return None


async def get_schedule(date: str, country: str, web: bool = False) -> list[dict] | None:
    """Episodes airing on ``date`` (YYYY-MM-DD) with the show embedded.

    ``web`` selects streaming releases instead of broadcast TV.  None on error.
    """
    path = "/schedule/web" if web else "/schedule"
    params = {"date": date, "country": country}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await _get_json(client, path, params)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else None
    except Exception as exc:
        logger.warning("TVMaze schedule failed (%s %s %s): %s", path, date, country, exc)
        return None


async def get_show_status(title: str, tvmaze_id: int | None = None) -> tuple[str, int | None]:
    """Return (status_string, tvmaze_id) for the named show.

    Uses the cached tvmaze_id when available to skip the search step.
    Status strings are normalised through _STATUS_MAP; anything not in the
    map (including a missing/null status field) maps to "Unknown".
    Returns ("Unknown", None) on any network or API error.
    """
    show: dict | None = None

    if tvmaze_id is not None:
        show = await get_show(tvmaze_id)

    if show is None:
        show = await search_show(title)

    if show is None:
        return ("Unknown", None)

    resolved_id: int | None = show.get("id")
    raw_status: str | None = show.get("status")
    status = _STATUS_MAP.get(raw_status or "", "Unknown")
    return (status, resolved_id)
