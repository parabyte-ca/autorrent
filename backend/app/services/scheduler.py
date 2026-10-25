import asyncio
import logging
import re
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="UTC")

# Stop-words excluded when matching query terms against result titles.
_STOP_WORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "is", "to", "at", "for", "on",
})


def scan_watchlist(target_item_id: int | None = None) -> None:
    """Scan all enabled watchlist items and auto-download new episodes."""
    from sqlalchemy.exc import IntegrityError
    from ..database import SessionLocal
    from ..models import Download, DownloadHistory, DownloadPath, Setting, WatchlistEpisode, WatchlistItem
    from .apprise_notify import notify
    from . import episode_guide
    from .candidates import (
        WATCHLIST_LOCK, effective_item, failed_hashes, failed_titles, find_with_fallback,
        parse_episode_tag, query_words, title_has_query_word,
    )
    from .indexers import search_all
    from .qbittorrent import add_torrent

    # Hold the lock for the whole scan so the retry queue cannot grab the same
    # episode concurrently.
    WATCHLIST_LOCK.acquire()
    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
        min_seeds = int(settings.get("min_seeds", "3"))
        category = settings.get("qbit_category", "autorrent")
        apprise_url = settings.get("apprise_url", "")

        query = db.query(WatchlistItem).filter(WatchlistItem.enabled == True)
        if target_item_id is not None:
            query = query.filter(WatchlistItem.id == target_item_id)
        items = query.all()
        logger.info("Watchlist scan started — %d item(s)", len(items))

        for item in items:
            item.last_checked = datetime.now(timezone.utc)
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

                ep_tag = f"S{item.season:02d}E{item.episode:02d}"
                words = query_words(item.search_query)
                eff = effective_item(db, item)
                results = find_with_fallback(
                    eff, item.season, item.episode, min_seeds,
                    failed_hashes(db, item.id, item.season, item.episode),
                    failed_titles(db, item.id, item.season, item.episode),
                )

                # Season pack: if enabled, also search for a full-season torrent
                # and prefer it over a single episode when one is found.
                use_pack = False
                pack_ok = False
                if eff.prefer_season_packs:
                    # A pack only exists once the season has finished airing, so
                    # skip the extra search while TVMaze says it is still running.
                    # Unknown (no TVMaze data) keeps the old behaviour: look anyway.
                    complete = episode_guide.season_complete(item.tvmaze_id, item.season)
                    try:
                        max_attempts = int(settings.get("retry_max_attempts", "3"))
                    except ValueError:
                        max_attempts = 3
                    # After repeated failed packs, stop preferring packs and fall
                    # back to grabbing episodes individually.
                    pack_failures = sum(
                        1 for d in db.query(Download).filter(
                            Download.watchlist_id == item.id,
                            Download.status.in_(["failed", "error"]),
                        ).all()
                        if parse_episode_tag(d.title) is None
                    )
                    pack_ok = complete is not False and pack_failures < max_attempts
                if pack_ok:
                    pack_query = f"{item.search_query} S{item.season:02d}"
                    pack_results = search_all(
                        pack_query,
                        quality=eff.quality,
                        codec=eff.codec or "x265",
                        filter_adult=True,
                    )
                    pack_results = [r for r in pack_results if r["seeds"] >= min_seeds]
                    # Must NOT have an episode number — we want packs, not single eps.
                    _ep_pattern = re.compile(r"[Ss]\d+[Ee]\d+")
                    pack_results = [r for r in pack_results if not _ep_pattern.search(r["title"])]
                    pack_results = [
                        r for r in pack_results if title_has_query_word(r["title"], words)
                    ]
                    bad_hashes = failed_hashes(db, item.id)
                    bad_titles = failed_titles(db, item.id)
                    pack_results = [
                        r for r in pack_results
                        if r["title"].lower() not in bad_titles
                        and (r.get("info_hash") or "").lower() not in bad_hashes
                    ]
                    if eff.max_size_mb:
                        max_bytes = eff.max_size_mb * 1024 * 1024
                        pack_results = [r for r in pack_results if r.get("size_bytes", 0) <= max_bytes]
                    if pack_results:
                        best_pack = max(pack_results, key=lambda r: r["seeds"])
                        results = [best_pack]
                        use_pack = True
                        logger.info(
                            "Season pack preferred for '%s' S%02d: %s",
                            item.title, item.season, best_pack["title"],
                        )

                if not results:
                    logger.debug("No results for: %s", query)
                    continue

                # find_candidates already ranks by preferred group, then seeds.
                best = results[0]

                # Secondary guard: skip if a Download record already tracks this episode
                # (handles the case where the episode is actively downloading).
                existing = (
                    db.query(Download)
                    .filter(
                        Download.watchlist_id == item.id,
                        Download.title.ilike(f"%{ep_tag}%"),
                        Download.status.notin_(["failed", "error"]),
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

                # A pack covers every aired episode of the season; record them all so
                # the scanner and the episode guide treat the season as done.
                if use_pack:
                    recorded_numbers = (
                        episode_guide.aired_episode_numbers(item.tvmaze_id, downloaded_season)
                        or [downloaded_episode]
                    )
                    item.season += 1
                    item.episode = 1
                else:
                    recorded_numbers = [downloaded_episode]
                    item.episode += 1
                item.last_found = datetime.now(timezone.utc)
                db.commit()

                # Record the episode(s) as downloaded — best-effort, must not block the scan.
                try:
                    have = {
                        e.episode for e in db.query(WatchlistEpisode).filter_by(
                            watchlist_id=item.id, season=downloaded_season
                        )
                    }
                    for number in recorded_numbers:
                        if number in have:
                            continue
                        db.add(WatchlistEpisode(
                            watchlist_id=item.id,
                            season=downloaded_season,
                            episode=number,
                            torrent_hash=info_hash,
                            torrent_name=best["title"],
                        ))
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    logger.warning(
                        "WatchlistEpisode duplicate skipped: watchlist_id=%d S%02d",
                        item.id, downloaded_season,
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
                if settings.get("notify_on_grab", "true").strip().lower() == "true":
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
        WATCHLIST_LOCK.release()

    logger.info("Watchlist scan complete")
    _sync_download_statuses()
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

            item.show_status_checked_at = datetime.now(timezone.utc)

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


def _sync_download_statuses() -> None:
    """Poll qBittorrent for all active downloads and advance their status in the DB.

    This runs on every scan cycle so that completed torrents are detected even
    when nobody has the Downloads page open.  Without this, a torrent that
    finishes (and is removed from qBittorrent) between page loads stays stuck
    at 'downloading' forever and is never picked up by _cleanup_completed().
    """
    from ..database import SessionLocal
    from ..models import Download, DownloadHistory
    from .qbittorrent import get_all_torrent_statuses

    _COMPLETE = {
        "uploading", "stalledUP", "checkingUP", "forcedUP",
        "pausedUP", "queuedUP", "completed", "moving",
    }

    db = SessionLocal()
    try:
        active = (
            db.query(Download)
            .filter(
                Download.status == "downloading",
                Download.qbit_removed == False,
                Download.torrent_hash != None,
            )
            .all()
        )

        if not active:
            return

        # Fetch all statuses in one API call.  None = qBittorrent unreachable;
        # skip updates entirely so we don't falsely mark torrents completed.
        all_statuses = get_all_torrent_statuses()
        if all_statuses is None:
            logger.warning("Status sync: qBittorrent unreachable — skipping.")
            return

        for dl in active:
            try:
                qs = all_statuses.get(dl.torrent_hash)

                if qs is None:
                    # Absent from qBittorrent (not just unreachable) — treat as completed.
                    dl.status = "completed"
                    dl.qbit_removed = True
                    if dl.completion_first_seen_at is None:
                        dl.completion_first_seen_at = datetime.now(timezone.utc)
                    try:
                        hist = (
                            db.query(DownloadHistory)
                            .filter(
                                DownloadHistory.torrent_hash == dl.torrent_hash,
                                DownloadHistory.status == "downloading",
                            )
                            .order_by(DownloadHistory.added_at.desc())
                            .first()
                        )
                        if hist:
                            hist.status = "completed"
                            hist.completed_at = datetime.now(timezone.utc)
                    except Exception:
                        pass
                    logger.info(
                        "Status sync: '%s' gone from qBittorrent — marked completed.",
                        dl.title,
                    )

                elif qs.get("status") in _COMPLETE:
                    if dl.status != "completed":
                        dl.status = "completed"
                        # Also update the history record
                        try:
                            hist = (
                                db.query(DownloadHistory)
                                .filter(
                                    DownloadHistory.torrent_hash == dl.torrent_hash,
                                    DownloadHistory.status == "downloading",
                                )
                                .order_by(DownloadHistory.added_at.desc())
                                .first()
                            )
                            if hist:
                                hist.status = "completed"
                                hist.completed_at = datetime.now(timezone.utc)
                        except Exception:
                            pass
                    if dl.completion_first_seen_at is None and qs.get("status") != "moving":
                        dl.completion_first_seen_at = datetime.now(timezone.utc)
                    if dl.size_bytes is None and qs.get("size"):
                        dl.size_bytes = qs["size"]
                    logger.debug("Status sync: '%s' marked completed.", dl.title)

            except Exception as e:
                logger.error("Status sync failed for '%s': %s", dl.title, e)

        db.commit()

    except Exception as e:
        logger.error("Download status sync failed: %s", e)
    finally:
        db.close()


def _cleanup_completed() -> None:
    """Remove completed torrents from qBittorrent when remove_on_complete is enabled."""
    import threading
    from ..database import SessionLocal
    from ..models import Download, Setting
    from .media_servers import trigger_media_refresh
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
        fired_refresh = False
        for dl in candidates:
            if not dl.torrent_hash:
                continue
            try:
                remove_torrent(dl.torrent_hash, delete_files=False)
                dl.qbit_removed = True
                fired_refresh = True
                logger.info("Removed completed torrent from qBittorrent: %s", dl.title)
            except Exception as e:
                logger.warning("Could not remove torrent %s: %s", dl.torrent_hash, e)

        db.commit()

        if fired_refresh:
            def _do_refresh():
                try:
                    asyncio.run(trigger_media_refresh(settings))
                except Exception as e:
                    logger.warning("Media refresh failed after cleanup: %s", e)
            threading.Thread(target=_do_refresh, daemon=True).start()

    except Exception as e:
        logger.error("Cleanup failed: %s", e)
    finally:
        db.close()


def run_weekly_digest() -> None:
    """Send the weekly Plex content digest email."""
    from ..database import SessionLocal
    from ..models import Setting
    from .plex_digest import fetch_digest_sections, render_html, send_email

    db = SessionLocal()
    try:
        s = {row.key: row.value for row in db.query(Setting).all()}
    finally:
        db.close()

    if s.get("digest_enabled", "false").lower() != "true":
        return

    # Deduplication guard: skip if already sent within the past 12 hours.
    # This prevents double-sends if the scheduler fires twice (e.g. container
    # restart at the scheduled hour, or two replicas running simultaneously).
    last_sent_str = s.get("last_digest_sent_at", "")
    if last_sent_str:
        try:
            last_sent = datetime.fromisoformat(last_sent_str)
            if (datetime.now(timezone.utc) - last_sent).total_seconds() < 12 * 3600:
                logger.info(
                    "Weekly digest skipped — already sent at %s (within 12 h window).",
                    last_sent_str,
                )
                return
        except Exception:
            pass

    recipients_raw = s.get("digest_recipients", "") or ""
    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        logger.info("Weekly digest skipped — no recipients configured.")
        return

    plex_url   = s.get("plex_url", "")
    plex_token = s.get("plex_token", "")
    if not plex_url or not plex_token:
        logger.warning("Weekly digest skipped — Plex not configured.")
        return

    smtp_host = s.get("digest_smtp_host", "")
    if not smtp_host:
        logger.warning("Weekly digest skipped — SMTP host not configured.")
        return

    excluded_raw = s.get("digest_excluded_libs", "") or ""
    excluded_libs = frozenset(
        x.strip().lower() for x in excluded_raw.replace("\n", ",").split(",") if x.strip()
    )

    try:
        sections = fetch_digest_sections(plex_url, plex_token, excluded_libs=excluded_libs)
    except Exception as e:
        logger.error("Digest: failed to fetch Plex data: %s", e)
        return

    now = datetime.now(timezone.utc)
    week_label = f"Week of {now.strftime('%B %d, %Y')}"
    html = render_html(sections, week_label)

    smtp_cfg = {
        "host":       smtp_host,
        "port":       s.get("digest_smtp_port", "587"),
        "user":       s.get("digest_smtp_user", ""),
        "password":   s.get("digest_smtp_password", ""),
        "from_email": s.get("digest_from_email", ""),
    }
    try:
        send_email(smtp_cfg, recipients, html, week_label)
        # Record send time so the deduplication guard above works correctly.
        _record_digest_sent()
    except Exception as e:
        logger.error("Digest send failed: %s", e)


def _record_digest_sent() -> None:
    from ..database import SessionLocal
    from ..models import Setting

    db = SessionLocal()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        row = db.query(Setting).filter(Setting.key == "last_digest_sent_at").first()
        if row:
            row.value = ts
        else:
            db.add(Setting(key="last_digest_sent_at", value=ts))
        db.commit()
    except Exception as e:
        logger.error("Failed to record digest send timestamp: %s", e)
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


def _get_digest_schedule() -> tuple[str, int]:
    from ..database import SessionLocal
    from ..models import Setting

    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
        day = settings.get("digest_day_of_week") or "mon"
        hour_str = settings.get("digest_hour")
        hour = int(hour_str) if hour_str is not None and hour_str != "" else 8
        return day, hour
    except Exception:
        return "mon", 8
    finally:
        db.close()


def start_scheduler() -> None:
    from .download_events import process_download_events
    from .updater import daily_update_check

    interval = _get_interval()
    _scheduler.add_job(
        scan_watchlist,
        trigger=IntervalTrigger(minutes=interval),
        id="watchlist_scan",
        replace_existing=True,
    )
    _scheduler.add_job(
        process_download_events,
        trigger=IntervalTrigger(minutes=10),
        id="download_events",
        replace_existing=True,
    )
    _scheduler.add_job(
        check_show_statuses,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="UTC"),
        id="show_status_check",
        replace_existing=True,
    )
    digest_day, digest_hour = _get_digest_schedule()
    _scheduler.add_job(
        run_weekly_digest,
        trigger=CronTrigger(day_of_week=digest_day, hour=digest_hour, minute=0, timezone="UTC"),
        id="weekly_digest",
        replace_existing=True,
    )
    _scheduler.add_job(
        daily_update_check,
        trigger=CronTrigger(hour=3, minute=17, timezone="UTC"),
        id="update_check",
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


def reschedule_digest(day: str, hour: int) -> None:
    if _scheduler.running:
        _scheduler.reschedule_job(
            "weekly_digest",
            trigger=CronTrigger(day_of_week=day, hour=hour, minute=0, timezone="UTC"),
        )
        logger.info("Digest schedule updated to %s at %02d:00 UTC", day, hour)
