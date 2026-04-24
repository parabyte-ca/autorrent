import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="UTC")


def scan_watchlist() -> None:
    """Scan all enabled watchlist items and auto-download new episodes."""
    from sqlalchemy.exc import IntegrityError
    from ..database import SessionLocal
    from ..models import Download, DownloadHistory, DownloadPath, Setting, WatchlistEpisode, WatchlistItem
    from .apprise_notify import notify
    from .indexers import search_all
    from .qbittorrent import add_torrent

    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
        min_seeds = int(settings.get("min_seeds", "3"))
        category = settings.get("qbit_category", "autorrent")
        apprise_url = settings.get("apprise_url", "")

        items = db.query(WatchlistItem).filter(WatchlistItem.enabled == True).all()
        logger.info("Watchlist scan started — %d item(s)", len(items))

        for item in items:
            item.last_checked = datetime.utcnow()
            db.commit()

            try:
                query = f"{item.search_query} S{item.season:02d}E{item.episode:02d}"

                # Skip if this S/E was already recorded in the persistent episode table.
                # This is the primary deduplication guard and survives torrent removal
                # from qBittorrent.
                already_ep = (
                    db.query(WatchlistEpisode)
                    .filter_by(
                        watchlist_id=item.id,
                        season=item.season,
                        episode=item.episode,
                    )
                    .first()
                )
                if already_ep:
                    logger.debug(
                        "Episode already tracked: watchlist_id=%d S%02dE%02d — skipping",
                        item.id, item.season, item.episode,
                    )
                    continue

                results = search_all(
                    query,
                    quality=item.quality,
                    codec=item.codec or "x265",
                    filter_adult=True,
                )
                results = [r for r in results if r["seeds"] >= min_seeds]

                if not results:
                    logger.debug("No results for: %s", query)
                    continue

                best = max(results, key=lambda r: r["seeds"])

                # Secondary guard: skip if a Download record already tracks this episode
                # (handles the case where the episode is actively downloading).
                ep_tag = f"S{item.season:02d}E{item.episode:02d}"
                existing = (
                    db.query(Download)
                    .filter(
                        Download.watchlist_id == item.id,
                        Download.title.ilike(f"%{ep_tag}%"),
                    )
                    .first()
                )
                if existing:
                    continue

                # Duplicate check — skip silently if already downloaded.
                from .duplicate import check_duplicate
                from .qbittorrent import _hash_from_magnet as _h
                dup = check_duplicate(
                    torrent_hash=_h(best["magnet"]),
                    torrent_name=best["title"],
                    db=db,
                )
                if dup["is_duplicate"]:
                    logger.info(
                        "Skipping duplicate watchlist download: '%s' matched existing entry '%s' (%s).",
                        best["title"], dup["matched_name"], dup["match_type"],
                    )
                    continue

                # Resolve save path
                save_path = "/downloads"
                if item.download_path_id:
                    dp = db.query(DownloadPath).filter(DownloadPath.id == item.download_path_id).first()
                    if dp:
                        save_path = dp.path
                else:
                    dp = db.query(DownloadPath).filter(DownloadPath.is_default == True).first()
                    if dp:
                        save_path = dp.path

                # Capture S/E before incrementing so the record reflects what was downloaded.
                downloaded_season = item.season
                downloaded_episode = item.episode

                info_hash = add_torrent(best["magnet"], save_path, category)

                dl = Download(
                    title=best["title"],
                    torrent_hash=info_hash,
                    magnet_link=best["magnet"],
                    size_bytes=best["size_bytes"],
                    status="downloading",
                    download_path=save_path,
                    watchlist_id=item.id,
                )
                db.add(dl)

                item.episode += 1
                item.last_found = datetime.utcnow()
                db.commit()

                # Record the episode as downloaded — best-effort, must not block the scan.
                try:
                    ep = WatchlistEpisode(
                        watchlist_id=item.id,
                        season=downloaded_season,
                        episode=downloaded_episode,
                        torrent_hash=info_hash,
                        torrent_name=best["title"],
                    )
                    db.add(ep)
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    logger.warning(
                        "WatchlistEpisode duplicate skipped: watchlist_id=%d S%02dE%02d",
                        item.id, downloaded_season, downloaded_episode,
                    )
                except Exception as ep_err:
                    logger.error("Failed to write WatchlistEpisode: %s", ep_err)

                # Write history record — best-effort; must not raise or delay the scan.
                try:
                    hist = DownloadHistory(
                        name=best["title"],
                        source="watchlist",
                        # best["source"] is the indexer slug, e.g. "nyaa", "tpb"
                        indexer=best.get("source"),
                        folder=dp.name if dp else None,
                        torrent_hash=info_hash,
                        size_bytes=best.get("size_bytes"),
                        status="downloading",
                        watchlist_id=item.id,
                    )
                    db.add(hist)
                    db.commit()
                except Exception as hist_err:
                    logger.error("Failed to write watchlist download history: %s", hist_err)

                logger.info("Auto-downloaded: %s", best["title"])
                notify(
                    apprise_url,
                    title=f"AutoRrent — {item.title}",
                    body=f"⬇️ Downloaded: {best['title']}",
                )

            except Exception as e:
                logger.error("Error processing watchlist item %d: %s", item.id, e)

    except Exception as e:
        logger.error("Scan failed: %s", e)
    finally:
        db.close()

    logger.info("Watchlist scan complete")
    _cleanup_completed()


async def _check_statuses_async(db, *, item_id: int | None = None) -> None:
    """Check TVMaze show status for watchlist items and auto-pause ended shows."""
    from ..models import Setting, WatchlistItem
    from .apprise_notify import notify
    from .tvmaze import get_show_status

    settings = {s.key: s.value for s in db.query(Setting).all()}
    apprise_url = settings.get("apprise_url", "")

    query = db.query(WatchlistItem).filter(WatchlistItem.enabled == True)
    if item_id is not None:
        query = query.filter(WatchlistItem.id == item_id)
    items = query.all()

    logger.info("Show status check started — %d item(s)", len(items))

    for item in items:
        try:
            status, tvmaze_id = await get_show_status(item.title, item.tvmaze_id)

            item.show_status_checked_at = datetime.utcnow()

            if status != "Unknown":
                item.show_status = status
            if tvmaze_id is not None:
                item.tvmaze_id = tvmaze_id

            if status == "Ended" and not item.show_status_override:
                item.enabled = False
                db.commit()
                logger.info(
                    "Auto-paused watchlist item %d (%r) — show has ended",
                    item.id, item.title,
                )
                notify(
                    apprise_url,
                    title=f"AutoRrent — {item.title}",
                    body=f"{item.title} has ended — auto-paused on watchlist.",
                )
            else:
                db.commit()

        except Exception as exc:
            logger.error("Status check failed for item %d: %s", item.id, exc)

    logger.info("Show status check complete")


def check_show_statuses(item_id: int | None = None) -> None:
    """Sync wrapper — safe to call from APScheduler threads."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        asyncio.run(_check_statuses_async(db, item_id=item_id))
    except Exception as exc:
        logger.error("check_show_statuses failed: %s", exc)
    finally:
        db.close()


def _cleanup_completed() -> None:
    """Remove completed torrents from qBittorrent when remove_on_complete is enabled.

    Updated in v2.1 to query 'completed' status (replacing old 'seeding') and
    to set qbit_removed=True instead of status='done', keeping status consistent
    with the polling-loop auto-cleanup in the downloads router.
    """
    from ..database import SessionLocal
    from ..models import Download, Setting
    from .qbittorrent import remove_torrent

    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
        if settings.get("remove_on_complete", "false").lower() != "true":
            return

        # Include legacy "seeding" rows created before v2.1.
        candidates = (
            db.query(Download)
            .filter(
                Download.status.in_(["seeding", "completed"]),
                Download.qbit_removed == False,
            )
            .all()
        )
        for dl in candidates:
            if not dl.torrent_hash:
                continue
            try:
                remove_torrent(dl.torrent_hash, delete_files=False)
                dl.qbit_removed = True
                logger.info("Removed completed torrent from qBittorrent: %s", dl.title)
            except Exception as e:
                logger.warning("Could not remove torrent %s: %s", dl.torrent_hash, e)

        db.commit()
    except Exception as e:
        logger.error("Cleanup failed: %s", e)
    finally:
        db.close()


def _get_interval() -> int:
    from ..database import SessionLocal
    from ..models import Setting

    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "scan_interval_minutes").first()
        return int(s.value) if s and s.value else 60
    except Exception:
        return 60
    finally:
        db.close()


def start_scheduler() -> None:
    interval = _get_interval()
    _scheduler.add_job(
        scan_watchlist,
        trigger=IntervalTrigger(minutes=interval),
        id="watchlist_scan",
        replace_existing=True,
    )
    _scheduler.add_job(
        check_show_statuses,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="UTC"),
        id="show_status_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — interval: %d min", interval)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def get_scheduler() -> BackgroundScheduler:
    """Return the module-level scheduler singleton.

    Exposed so other modules (e.g. the backup router) can pause/resume the
    scheduler without importing the private ``_scheduler`` name directly.
    """
    return _scheduler


def update_interval(minutes: int) -> None:
    if _scheduler.running:
        _scheduler.reschedule_job(
            "watchlist_scan",
            trigger=IntervalTrigger(minutes=minutes),
        )
        logger.info("Scheduler interval updated to %d min", minutes)
