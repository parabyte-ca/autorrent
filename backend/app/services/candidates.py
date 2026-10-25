"""Ranked torrent-candidate search for a single watchlist episode.

Shared by the watchlist scanner and the retry queue so both pick releases by
exactly the same rules.
"""
import logging
import re
import threading
from types import SimpleNamespace

from .qbittorrent import _hash_from_magnet

logger = logging.getLogger(__name__)

# Serialises scanner and retry-queue work on watchlist items so the two
# background jobs can never grab the same episode twice.
WATCHLIST_LOCK = threading.RLock()

STOP_WORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "is", "to", "at", "for", "on",
})

_SXXEXX = re.compile(r"[Ss](\d+)[Ee](\d+)")


def query_words(search_query: str) -> set[str]:
    return {
        w for w in re.sub(r"[^a-z0-9]", " ", search_query.lower()).split()
        if len(w) > 1 and w not in STOP_WORDS
    }


def title_has_query_word(title: str, words: set[str]) -> bool:
    if not words:
        return True
    return bool(words & set(re.sub(r"[^a-z0-9]", " ", title.lower()).split()))


def result_hash(result: dict) -> str | None:
    h = (result.get("info_hash") or "").lower()
    return h or _hash_from_magnet(result.get("magnet", ""))


def parse_episode_tag(title: str) -> tuple[int, int] | None:
    m = _SXXEXX.search(title)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _failed_downloads(db, watchlist_id: int, season: int | None, episode: int | None):
    from ..models import Download

    q = db.query(Download).filter(
        Download.watchlist_id == watchlist_id,
        Download.status.in_(["failed", "error"]),
    )
    if season is not None and episode is not None:
        q = q.filter(Download.title.ilike(f"%S{season:02d}E{episode:02d}%"))
    return q.all()


def failed_hashes(db, watchlist_id: int, season: int | None = None,
                  episode: int | None = None) -> set[str]:
    """Info-hashes of failed/stalled attempts — never re-grab them.

    With season+episode, only attempts at that episode; without, every failed
    attempt for the item (used to avoid re-grabbing a failed season pack).
    """
    return {
        r.torrent_hash.lower()
        for r in _failed_downloads(db, watchlist_id, season, episode) if r.torrent_hash
    }


def failed_titles(db, watchlist_id: int, season: int | None = None,
                  episode: int | None = None) -> set[str]:
    """Titles of failed attempts; backs up the hash check for URL-only results."""
    return {r.title.lower() for r in _failed_downloads(db, watchlist_id, season, episode)}


def effective_item(db, item):
    """The download preferences actually used for ``item``.

    When the item has a quality profile, the profile's values override the
    item's own quality/codec/size/groups/season-pack fields.  Returns a plain
    namespace so the real ORM row is never mutated by accident.
    """
    from ..models import QualityProfile

    profile = None
    if getattr(item, "profile_id", None):
        profile = db.query(QualityProfile).filter(QualityProfile.id == item.profile_id).first()
    src = profile or item
    return SimpleNamespace(
        id=item.id,
        title=item.title,
        search_query=item.search_query,
        tvmaze_id=item.tvmaze_id,
        quality=src.quality,
        codec=src.codec,
        max_size_mb=src.max_size_mb,
        preferred_groups=src.preferred_groups,
        prefer_season_packs=bool(src.prefer_season_packs),
        fallback_quality=getattr(profile, "fallback_quality", None) if profile else None,
        fallback_after_hours=getattr(profile, "fallback_after_hours", 24) if profile else 24,
    )


def find_candidates(
    item,
    season: int,
    episode: int,
    min_seeds: int,
    exclude_hashes: set[str] | frozenset[str] = frozenset(),
    exclude_titles: set[str] | frozenset[str] = frozenset(),
) -> list[dict]:
    """Return acceptable releases for S{season}E{episode}, best first."""
    from .indexers import search_all

    ep_tag = f"S{season:02d}E{episode:02d}"
    results = search_all(
        f"{item.search_query} {ep_tag}",
        quality=item.quality,
        codec=item.codec or "x265",
        filter_adult=True,
    )
    results = [r for r in results if r["seeds"] >= min_seeds]

    # Some indexers (e.g. EZTV) ignore the query, so verify the episode tag and
    # that at least one significant word of the show name is in the title.
    results = [r for r in results if ep_tag.lower() in r["title"].lower()]
    words = query_words(item.search_query)
    results = [r for r in results if title_has_query_word(r["title"], words)]

    if item.max_size_mb:
        max_bytes = item.max_size_mb * 1024 * 1024
        results = [r for r in results if r.get("size_bytes", 0) <= max_bytes]

    if exclude_hashes:
        results = [r for r in results if result_hash(r) not in exclude_hashes]
    if exclude_titles:
        results = [r for r in results if r["title"].lower() not in exclude_titles]

    preferred = [
        g.strip().lower()
        for g in (item.preferred_groups or "").split(",")
        if g.strip()
    ]

    def group_score(r: dict) -> int:
        t = r["title"].lower()
        for rank, grp in enumerate(preferred):
            if grp in t:
                return len(preferred) - rank
        return 0

    return sorted(results, key=lambda r: (group_score(r), r["seeds"]), reverse=True)


def find_with_fallback(
    item,
    season: int,
    episode: int,
    min_seeds: int,
    exclude_hashes: set[str] | frozenset[str] = frozenset(),
    exclude_titles: set[str] | frozenset[str] = frozenset(),
) -> list[dict]:
    """find_candidates, then the profile's fallback quality once the episode is old enough.

    ``item`` must come from :func:`effective_item`.  The fallback only applies
    when TVMaze tells us the episode aired at least ``fallback_after_hours`` ago;
    with no air date we never lower quality.
    """
    results = find_candidates(item, season, episode, min_seeds, exclude_hashes, exclude_titles)
    if results or not item.fallback_quality or item.fallback_quality == item.quality:
        return results

    from . import episode_guide

    age = episode_guide.episode_age_hours(item.tvmaze_id, season, episode)
    if age is None or age < item.fallback_after_hours:
        return results

    fallback = SimpleNamespace(**{**vars(item), "quality": item.fallback_quality})
    results = find_candidates(fallback, season, episode, min_seeds, exclude_hashes, exclude_titles)
    if results:
        logger.info("%s S%02dE%02d: nothing at %s after %.0fh — using fallback %s",
                    item.title, season, episode, item.quality, age, item.fallback_quality)
    return results
