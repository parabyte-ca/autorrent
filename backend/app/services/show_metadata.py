"""Turn TVMaze show records into watchlist metadata (poster, blurb, network…)."""
import html
import logging
import re
from datetime import datetime, timezone

from . import tvmaze

logger = logging.getLogger(__name__)

_BLOCK_BREAKS = re.compile(r"</p>|<br\s*/?>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_NAME_SUFFIX = re.compile(r"\s*\((?:US|UK|AU|CA|NZ|\d{4})\)\s*$")
_SUMMARY_MAX = 600


def clean_summary(raw: str | None, limit: int = _SUMMARY_MAX) -> str | None:
    if not raw:
        return None
    # Paragraph/line breaks separate words; inline tags (<b>, <i>) must not, or
    # "<b>Name</b>." would become "Name ."
    text = html.unescape(_TAGS.sub("", _BLOCK_BREAKS.sub(" ", raw)))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def poster_url(show: dict) -> str | None:
    img = show.get("image") or {}
    url = img.get("medium") or img.get("original")
    if not url:
        return None
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def network_name(show: dict) -> str | None:
    for key in ("network", "webChannel"):
        name = (show.get(key) or {}).get("name")
        if name:
            return name
    return None


def search_query_from_name(name: str) -> str:
    """TVMaze disambiguates remakes as 'Name (US)' / 'Name (2019)'; indexers don't."""
    return _NAME_SUFFIX.sub("", name).strip() or name


def apply_show(item, show: dict) -> None:
    """Copy TVMaze details onto a WatchlistItem (never touches download settings)."""
    item.tvmaze_id = show.get("id") or item.tvmaze_id
    mapped = tvmaze._STATUS_MAP.get(show.get("status") or "")
    if mapped:
        item.show_status = mapped
        item.show_status_checked_at = datetime.now(timezone.utc)
    item.poster_url = poster_url(show) or item.poster_url
    item.summary = clean_summary(show.get("summary")) or item.summary
    item.network = network_name(show) or item.network
    item.premiered = show.get("premiered") or item.premiered


async def enrich_item(item) -> bool:
    """Look the show up on TVMaze and fill in metadata. Always marks the attempt.

    Returns True if TVMaze knew the show.  Failures are swallowed — metadata is
    cosmetic and must never block adding a show.
    """
    show = None
    try:
        if item.tvmaze_id:
            show = await tvmaze.get_show(item.tvmaze_id)
        if show is None:
            show = await tvmaze.search_show(item.search_query or item.title)
    except Exception as exc:  # defensive: tvmaze helpers already catch network errors
        logger.warning("Metadata lookup failed for %r: %s", item.title, exc)
    item.metadata_checked_at = datetime.now(timezone.utc)
    if show is None:
        return False
    apply_show(item, show)
    return True


def starting_point(episodes: list[dict] | None, status: str | None, mode: str | None,
                   now: datetime | None = None) -> tuple[int, int]:
    """Where a newly added show should start looking: (season, episode).

    ``mode`` is "beginning", "next" or None (None picks "next" for shows that are
    still running, otherwise "beginning").  "next" is the first episode that has
    not aired yet; if every known episode has aired it is the start of the
    following season.
    """
    if not episodes:
        return (1, 1)
    now = now or datetime.now(timezone.utc)
    ordered = sorted(episodes, key=lambda e: (e["season"], e["number"]))
    if mode is None:
        mode = "next" if status == "Running" else "beginning"
    if mode == "beginning":
        return (ordered[0]["season"], ordered[0]["number"])
    for e in ordered:
        if e["airs_at"] is not None and e["airs_at"] > now:
            return (e["season"], e["number"])
    return (ordered[-1]["season"] + 1, 1)
