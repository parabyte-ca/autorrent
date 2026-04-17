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


async def search_show(title: str) -> dict | None:
    """Search TVMaze for the best-matching show by title.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_BASE}/singlesearch/shows", params={"q": title})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("TVMaze search failed for %r: %s", title, exc)
        return None


async def get_show(tvmaze_id: int) -> dict | None:
    """Fetch a show by its TVMaze ID.

    Returns the show dict on success, None if not found or on error.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_BASE}/shows/{tvmaze_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("TVMaze get_show failed for id=%d: %s", tvmaze_id, exc)
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
