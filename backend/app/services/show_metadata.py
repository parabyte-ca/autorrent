"""Turn TVMaze show records into watchlist metadata (poster, blurb, network…)."""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from . import tmdb, tvmaze

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


def apply_movie(item, movie: dict) -> None:
    """Copy TMDB details onto a WatchlistItem (never touches download settings)."""
    item.poster_url = tmdb.poster_url(movie) or item.poster_url
    item.summary = clean_summary(movie.get("overview")) or item.summary
    item.premiered = movie.get("release_date") or item.premiered


FOUND = "found"
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"

# Things that follow a show's name in a release title.
_RELEASE_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:S\d{1,2}(?:E\d{1,3})*|Season ?\d+|(?:480|576|720|1080|2160)[pi]|4k|uhd"
    r"|web ?dl|web ?rip|hdtv|hdrip|dvdrip|bdrip|brrip|blu ?ray|remux|x26[45]|h ?26[45]|hevc|av1|xvid"
    r"|proper|repack)(?![A-Za-z0-9])"
)
_YEAR = re.compile(r"(?<![A-Za-z0-9])((?:19|20)\d{2})(?![A-Za-z0-9])")
_BRACKETS = re.compile(r"\[[^\]]*\]")


def parse_title(text: str) -> tuple[str, int | None]:
    """Split a stored title or release name into (show name, year hint).

    "Best.Pancakes.in.the.Country.2025.1080p.WEB-DL" -> ("Best Pancakes in the Country", 2025)
    "War 2026" -> ("War", 2026);  "Doctor Who (2005)" -> ("Doctor Who", 2005)
    A leading number is part of the name ("1883", "2012"), never a year hint.
    """
    t = _BRACKETS.sub(" ", text).replace("_", " ").strip()
    if " " not in t and t.count(".") >= 2:        # release-style "Some.Show.Name.2025.1080p"
        t = t.replace(".", " ")
    t = re.sub(r"\s+", " ", t)

    cuts = []
    year = None
    for m in _YEAR.finditer(t):
        if m.start() > 0:
            year = int(m.group(1))
            cuts.append(m.start())
            break
    for m in _RELEASE_TOKEN.finditer(t):
        if m.start() > 0:
            cuts.append(m.start())
            break
    name = t[: min(cuts)] if cuts else t
    name = re.sub(r"[\s\-–:(\[,]+$", "", name).strip()
    return (name or text.strip()), year


def _year_matches(show: dict, year: int) -> bool:
    premiered = (show.get("premiered") or "")[:4]
    return premiered.isdigit() and abs(int(premiered) - year) <= 1


async def find_show(texts: list[str | None]) -> dict | None:
    """Find a show from candidate titles (tried in order). Raises tvmaze.TVMazeUnavailable.

    A year in the text must match the show's premiere year (so "War 2026" is not
    answered with a different show called "War"); without one, TVMaze's best
    fuzzy match is used.
    """
    tried: set[tuple[str, int | None]] = set()
    for text in texts:
        if not text or not text.strip():
            continue
        name, year = parse_title(text)
        key = (name.lower(), year)
        if key in tried:
            continue
        tried.add(key)
        if year is not None:
            for show in await tvmaze.lookup_many(name):
                if _year_matches(show, year):
                    return show
            continue
        show = await tvmaze.lookup_by_name(name)
        if show:
            return show
    return None


def metadata_due(item, now: datetime | None = None) -> bool:
    """Should we (re)try to find a poster for this show?

    Never looked up: yes.  Looked up without finding one: retry after a growing
    delay (1, 2, 4, then 7 days) — a movie or an unknown title would otherwise
    be re-queried on every page load.
    """
    if item.poster_url:
        return False
    if item.metadata_checked_at is None:
        return True
    checked = item.metadata_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    attempts = max(item.metadata_attempts or 0, 1)
    wait = min(timedelta(days=7), timedelta(days=1) * 2 ** (attempts - 1))
    return (now or datetime.now(timezone.utc)) - checked >= wait


async def enrich_item(item, tmdb_key: str | None = None) -> str:
    """Look the title up — TVMaze for shows, then TMDB for movies — and fill in metadata.

    Returns FOUND, NOT_FOUND (the sources answered: no such title) or UNAVAILABLE
    (one could not answer — network, rate limit, outage, bad key).  Only an actual
    answer is recorded, so a temporary failure is retried instead of being
    remembered as "this title has no poster".  Never raises: metadata is cosmetic.

    TMDB is only asked when ``tmdb_key`` is given and TVMaze has no such show.
    """
    movie = None
    try:
        show = None
        if item.tvmaze_id:
            show = await tvmaze.fetch_show(item.tvmaze_id)
        if show is None:
            show = await find_show([item.search_query, item.title])
    except tvmaze.TVMazeUnavailable as exc:
        logger.warning("TVMaze unavailable while looking up %r: %s", item.title, exc)
        return UNAVAILABLE
    except Exception as exc:  # defensive: never let cosmetic data break the caller
        logger.warning("Metadata lookup failed for %r: %s", item.title, exc)
        return UNAVAILABLE

    if show is None and tmdb_key:
        try:
            movie = await find_movie(tmdb_key, [item.search_query, item.title])
        except tmdb.TMDBUnavailable as exc:
            logger.warning("TMDB unavailable while looking up %r: %s", item.title, exc)
            return UNAVAILABLE
        except Exception as exc:
            logger.warning("Movie lookup failed for %r: %s", item.title, exc)
            return UNAVAILABLE

    item.metadata_checked_at = datetime.now(timezone.utc)
    if show is not None:
        apply_show(item, show)
    elif movie is not None:
        apply_movie(item, movie)
    # Count consecutive lookups that produced no poster, to back off retries.
    item.metadata_attempts = 0 if item.poster_url else (item.metadata_attempts or 0) + 1
    return FOUND if (show is not None or movie is not None) else NOT_FOUND


async def find_movie(key: str, texts: list[str | None]) -> dict | None:
    """Find a movie from candidate titles (tried in order). Raises tmdb.TMDBUnavailable."""
    tried: set[tuple[str, int | None]] = set()
    for text in texts:
        if not text or not text.strip():
            continue
        name, year = parse_title(text)
        if (name.lower(), year) in tried:
            continue
        tried.add((name.lower(), year))
        movie = await tmdb.search_movie(key, name, year)
        if movie:
            return movie
    return None


def tmdb_key(db) -> str | None:
    """The saved TMDB key if it looks valid, else None (movie lookups are then skipped)."""
    from ..models import Setting
    row = db.query(Setting).filter(Setting.key == "tmdb_api_key").first()
    key = (row.value if row else "") or ""
    return key.strip() if tmdb.valid_key(key.strip()) else None


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
