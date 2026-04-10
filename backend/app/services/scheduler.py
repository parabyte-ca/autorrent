import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="UTC")


def scan_watchlist() -> None:
    """Scan all enabled watchlist items and auto-download new episodes."""
    from ..database import SessionLocal
    from ..models import Download, DownloadPath, Setting, WatchlistItem
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
                results = search_all(query, quality=item.quality)
                results = [r for r in results if r["seeds"] >= min_seeds]

                if not results:
                    logger.debug("No results for: %s", query)
                    continue

                best = max(results, key=lambda r: r["seeds"])

                # Skip if already tracked
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

                logger.info("Auto-downloaded: %s", best["title"])
                notify(
                    apprise_url,
                    title=f"AutoRrent — {item.title}",
                    body=f"Downloaded: {best['title']}",
                )

            except Exception as e:
                logger.error("Error processing watchlist item %d: %s", item.id, e)

    except Exception as e:
        logger.error("Scan failed: %s", e)
    finally:
        db.close()

    logger.info("Watchlist scan complete")


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
    _scheduler.start()
    logger.info("Scheduler started — interval: %d min", interval)


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def update_interval(minutes: int) -> None:
    if _scheduler.running:
        _scheduler.reschedule_job(
            "watchlist_scan",
            trigger=IntervalTrigger(minutes=minutes),
        )
        logger.info("Scheduler interval updated to %d min", minutes)
