"""Create a watchlist entry from a TVMaze show (Browse "Add" and Trakt sync)."""
from datetime import datetime, timezone

from . import episode_guide, show_metadata, tvmaze


class ShowNotFound(Exception):
    """TVMaze does not know the requested show."""


class AlreadyOnWatchlist(Exception):
    """A watchlist entry for this TVMaze show already exists."""


def default_profile_id(db) -> int | None:
    """The configured default quality profile, ignoring one that was deleted."""
    from ..models import QualityProfile, Setting

    row = db.query(Setting).filter(Setting.key == "default_profile_id").first()
    if row and row.value and row.value.isdigit():
        if db.query(QualityProfile).filter(QualityProfile.id == int(row.value)).first():
            return int(row.value)
    return None


async def create_from_tvmaze(db, tvmaze_id: int, start: str | None = None,
                             profile_id: int | None = None, show: dict | None = None):
    """Add a show, starting at its next new episode or the beginning.

    ``start`` is "next", "beginning" or None (None: next for running shows,
    beginning otherwise).  ``profile_id`` None means the default profile.  Pass
    ``show`` if the TVMaze record was already fetched.  Raises
    :class:`AlreadyOnWatchlist` / :class:`ShowNotFound`.
    """
    from ..models import WatchlistItem

    if db.query(WatchlistItem).filter(WatchlistItem.tvmaze_id == tvmaze_id).first():
        raise AlreadyOnWatchlist()
    if show is None:
        show = await tvmaze.get_show(tvmaze_id)
    if show is None:
        raise ShowNotFound()

    if profile_id is None:
        profile_id = default_profile_id(db)

    episodes = await episode_guide.aget_episodes(tvmaze_id)
    season, episode = show_metadata.starting_point(episodes, show.get("status"), start)
    name = show.get("name") or "Unknown show"
    item = WatchlistItem(
        title=name,
        search_query=show_metadata.search_query_from_name(name),
        season=season,
        episode=episode,
        profile_id=profile_id,
    )
    show_metadata.apply_show(item, show)
    item.metadata_checked_at = datetime.now(timezone.utc)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
