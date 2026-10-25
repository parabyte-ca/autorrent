"""Manually grab one specific episode of a watchlist show (guide / calendar buttons)."""
import logging

from sqlalchemy.exc import IntegrityError

from .candidates import (
    WATCHLIST_LOCK,
    effective_item,
    failed_hashes,
    failed_titles,
    find_with_fallback,
    result_hash,
)

logger = logging.getLogger(__name__)

_TRY_TOP = 5  # how many ranked results to consider before giving up on duplicates


def grab_episode(db, item, season: int, episode: int) -> dict:
    """Search for and start downloading S{season}E{episode} of ``item``.

    Returns ``{"status": ...}`` where status is one of ``started``,
    ``already_downloaded``, ``duplicate`` or ``not_found``.  Raises on a
    qBittorrent failure so the caller can report it.
    """
    from ..models import (
        Download, DownloadHistory, DownloadPath, Setting, WatchlistEpisode,
    )
    from .apprise_notify import notify
    from .duplicate import check_duplicate
    from .qbittorrent import add_torrent

    ep_tag = f"S{season:02d}E{episode:02d}"
    if db.query(WatchlistEpisode).filter_by(
        watchlist_id=item.id, season=season, episode=episode
    ).first():
        return {"status": "already_downloaded", "message": f"{ep_tag} is already marked as downloaded."}

    settings = {s.key: s.value for s in db.query(Setting).all()}
    try:
        min_seeds = int(settings.get("min_seeds", "3"))
    except ValueError:
        min_seeds = 3
    category = settings.get("qbit_category", "autorrent")

    with WATCHLIST_LOCK:
        candidates = find_with_fallback(
            effective_item(db, item), season, episode, min_seeds,
            failed_hashes(db, item.id, season, episode),
            failed_titles(db, item.id, season, episode),
        )
        if not candidates:
            return {"status": "not_found", "message": f"No suitable release found for {ep_tag} yet."}

        best = None
        for cand in candidates[:_TRY_TOP]:
            dup = check_duplicate(torrent_hash=result_hash(cand), torrent_name=cand["title"], db=db)
            if not dup["is_duplicate"]:
                best = cand
                break
        if best is None:
            return {"status": "duplicate",
                    "message": f"{ep_tag} looks like it was already downloaded (matches an existing entry)."}

        dp = None
        if item.download_path_id:
            dp = db.query(DownloadPath).filter(DownloadPath.id == item.download_path_id).first()
        if dp is None:
            dp = db.query(DownloadPath).filter(DownloadPath.is_default == True).first()  # noqa: E712
        save_path = dp.path if dp else "/downloads"

        info_hash = add_torrent(best["magnet"], save_path, category)

        db.add(Download(
            title=best["title"], torrent_hash=info_hash, magnet_link=best["magnet"],
            size_bytes=best.get("size_bytes"), status="downloading",
            download_path=save_path, watchlist_id=item.id,
        ))
        db.add(DownloadHistory(
            name=best["title"], source="watchlist", indexer=best.get("source"),
            folder=dp.name if dp else None, torrent_hash=info_hash,
            size_bytes=best.get("size_bytes"), status="downloading", watchlist_id=item.id,
        ))
        db.add(WatchlistEpisode(
            watchlist_id=item.id, season=season, episode=episode,
            torrent_hash=info_hash, torrent_name=best["title"],
        ))
        # Keep the scanner from fetching this episode again if it was next in line.
        if (item.season, item.episode) == (season, episode):
            item.episode += 1
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"status": "already_downloaded", "message": f"{ep_tag} was just downloaded."}

    if settings.get("notify_on_grab", "true").strip().lower() == "true":
        notify(settings.get("apprise_url", ""), title=f"AutoRrent — {item.title}",
               body=f"⬇️ Downloaded: {best['title']}")
    logger.info("Manual grab: %s", best["title"])
    return {"status": "started", "title": best["title"], "message": f"Started: {best['title']}"}
