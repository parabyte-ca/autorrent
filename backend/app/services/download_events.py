"""Download lifecycle events: one-time alerts and the automatic retry queue.

``process_download_events`` runs on its own schedule.  It is the single place
that decides a download has completed, failed or stalled *for alerting
purposes*, using per-download ``notified_*`` flags so each event fires once no
matter which code path (Downloads page poll, scan cycle, this sweep) changed
the underlying status.
"""
import logging
from datetime import datetime, timedelta, timezone

from .candidates import (
    WATCHLIST_LOCK,
    failed_hashes,
    find_candidates,
    parse_episode_tag,
)
from .qbit_states import ERROR_STATES

logger = logging.getLogger(__name__)

_DONE_STATUSES = ("completed", "seeding", "done")
_FAILED_STATUSES = ("error", "failed")
# States where a torrent is idle on purpose (paused, queued behind others, being
# checked/allocated).  They must never count as stalled.
_IDLE_OK_STATES = {
    "pausedDL", "pausedUP", "queuedDL", "checkingDL", "checkingResumeData", "allocating",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _flag(settings: dict, key: str, default: str = "true") -> bool:
    return (settings.get(key, default) or default).strip().lower() == "true"


def _int(settings: dict, key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _alert(settings: dict, key: str, title: str, body: str) -> None:
    from .apprise_notify import notify

    if _flag(settings, key):
        notify(settings.get("apprise_url", ""), title=title, body=body)


def _mark_history_failed(db, torrent_hash: str | None, msg: str) -> None:
    from ..models import DownloadHistory

    if not torrent_hash:
        return
    hist = (
        db.query(DownloadHistory)
        .filter(
            DownloadHistory.torrent_hash == torrent_hash,
            DownloadHistory.status == "downloading",
        )
        .order_by(DownloadHistory.added_at.desc())
        .first()
    )
    if hist:
        hist.status = "failed"
        hist.error_msg = msg


def _remove_from_qbit(dl) -> None:
    from .qbittorrent import remove_torrent

    if not dl.torrent_hash or dl.qbit_removed:
        return
    try:
        remove_torrent(dl.torrent_hash, delete_files=True)
    except Exception as exc:  # already gone, or qBittorrent unreachable
        logger.warning("Could not remove torrent %s: %s", dl.torrent_hash, exc)
    dl.qbit_removed = True


def retry_download(db, dl, settings: dict, reason: str) -> str:
    """Re-search a failed/stalled watchlist episode and grab the next-best release.

    Returns a short human-readable outcome used in the alert text.  The caller
    has already marked ``dl`` failed and removed it from qBittorrent.
    """
    from ..models import Download, DownloadHistory, WatchlistEpisode, WatchlistItem
    from .qbittorrent import add_torrent

    if not _flag(settings, "retry_enabled"):
        return ""
    if dl.watchlist_id is None:
        return ""

    tag = parse_episode_tag(dl.title)
    if tag is None:  # manual download or a season pack — nothing to re-search
        return ""
    season, episode = tag

    item = db.query(WatchlistItem).filter(WatchlistItem.id == dl.watchlist_id).first()
    if item is None:
        return ""

    ep_tag = f"S{season:02d}E{episode:02d}"
    max_attempts = _int(settings, "retry_max_attempts", 3)
    min_seeds = _int(settings, "min_seeds", 3)
    category = settings.get("qbit_category", "autorrent")

    with WATCHLIST_LOCK:
        excluded = failed_hashes(db, item.id, season, episode)
        if dl.torrent_hash:
            excluded.add(dl.torrent_hash.lower())

        def drop_episode_record(rollback_pointer: bool) -> None:
            db.query(WatchlistEpisode).filter_by(
                watchlist_id=item.id, season=season, episode=episode
            ).delete()
            if rollback_pointer and item.season == season and item.episode == episode + 1:
                item.episode = episode  # let the scanner try again next cycle
            db.commit()

        if len(excluded) >= max_attempts:
            drop_episode_record(rollback_pointer=False)
            return f"Gave up on {ep_tag} after {len(excluded)} attempts."

        try:
            candidates = find_candidates(item, season, episode, min_seeds, excluded)
        except Exception as exc:
            logger.error("Retry search failed for %s %s: %s", item.title, ep_tag, exc)
            candidates = []

        if not candidates:
            drop_episode_record(rollback_pointer=True)
            return f"No alternative release found for {ep_tag} yet — will keep checking."

        best = candidates[0]
        save_path = dl.download_path or "/downloads"
        try:
            info_hash = add_torrent(best["magnet"], save_path, category)
        except Exception as exc:
            logger.error("Retry add_torrent failed for %s: %s", best["title"], exc)
            drop_episode_record(rollback_pointer=True)
            return f"Could not start an alternative for {ep_tag} ({exc}) — will keep checking."

        new_dl = Download(
            title=best["title"],
            torrent_hash=info_hash,
            magnet_link=best["magnet"],
            size_bytes=best.get("size_bytes"),
            status="downloading",
            download_path=save_path,
            watchlist_id=item.id,
            retry_count=(dl.retry_count or 0) + 1,
            last_progress_at=_now(),
        )
        db.add(new_dl)

        old_hist = (
            db.query(DownloadHistory)
            .filter(DownloadHistory.torrent_hash == dl.torrent_hash)
            .order_by(DownloadHistory.added_at.desc())
            .first()
        )
        db.add(DownloadHistory(
            name=best["title"],
            source="watchlist",
            indexer=best.get("source"),
            folder=old_hist.folder if old_hist else None,
            torrent_hash=info_hash,
            size_bytes=best.get("size_bytes"),
            status="downloading",
            watchlist_id=item.id,
        ))

        ep_row = db.query(WatchlistEpisode).filter_by(
            watchlist_id=item.id, season=season, episode=episode
        ).first()
        if ep_row:
            ep_row.torrent_hash = info_hash
            ep_row.torrent_name = best["title"]
        else:
            db.add(WatchlistEpisode(
                watchlist_id=item.id, season=season, episode=episode,
                torrent_hash=info_hash, torrent_name=best["title"],
            ))
        db.commit()

    logger.info("Retry (%s): %s -> %s", reason, dl.title, best["title"])
    return f"Retrying with: {best['title']}"


def _fail_download(db, dl, settings: dict, reason: str, alert_key: str, flag_attr: str) -> None:
    """Mark ``dl`` failed, clean up qBittorrent, retry, and alert once."""
    dl.status = "failed"
    _remove_from_qbit(dl)
    _mark_history_failed(db, dl.torrent_hash, reason)
    # notified_failed is always set so the "failed outside this sweep" pass below
    # can never alert or retry the same download a second time.
    dl.notified_failed = True
    setattr(dl, flag_attr, True)
    db.commit()

    outcome = ""
    try:
        outcome = retry_download(db, dl, settings, reason)
    except Exception as exc:
        logger.error("Retry queue error for '%s': %s", dl.title, exc)

    icon = "⏳" if alert_key == "notify_on_stalled" else "❌"
    body = f"{icon} {dl.title} — {reason}."
    if outcome:
        body += f"\n{outcome}"
    _alert(settings, alert_key, "AutoRrent — Download problem", body)


def process_download_events() -> None:
    """Detect completions, failures and stalls; send alerts; run the retry queue."""
    from ..database import SessionLocal
    from ..models import Download, Setting
    from .disk_space import check_and_alert
    from .qbittorrent import get_all_torrent_statuses
    from .scheduler import _sync_download_statuses

    # Complete anything finished/removed first so the sweep sees current status.
    try:
        _sync_download_statuses()
    except Exception as exc:
        logger.error("Status sync before event sweep failed: %s", exc)

    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
        now = _now()
        stall_after = timedelta(hours=max(_int(settings, "stall_hours", 2), 1))

        # ── Active downloads: progress tracking, error and stall detection ────
        active = (
            db.query(Download)
            .filter(
                Download.status == "downloading",
                Download.qbit_removed == False,  # noqa: E712
                Download.torrent_hash != None,  # noqa: E711
            )
            .all()
        )
        statuses = get_all_torrent_statuses() if active else None

        if statuses is not None:
            for dl in active:
                qs = statuses.get((dl.torrent_hash or "").lower())
                if qs is None:
                    continue
                state = qs.get("status", "")

                if state in ERROR_STATES:
                    if not dl.notified_failed:
                        _fail_download(db, dl, settings, f"qBittorrent reported '{state}'",
                                       "notify_on_failed", "notified_failed")
                    continue

                progress = qs.get("progress") or 0.0
                if dl.last_progress_at is None or progress > (dl.last_progress or 0.0) + 1e-6:
                    dl.last_progress = progress
                    dl.last_progress_at = now
                    db.commit()
                    continue

                if state in _IDLE_OK_STATES:
                    dl.last_progress_at = now  # idle on purpose; restart the clock
                    db.commit()
                    continue

                idle = now - _aware(dl.last_progress_at)
                if idle >= stall_after and not dl.notified_stalled:
                    hours = int(idle.total_seconds() // 3600)
                    _fail_download(db, dl, settings, f"no progress for {hours}h",
                                   "notify_on_stalled", "notified_stalled")

        # ── One-time alerts for completed and failed downloads ────────────────
        for dl in db.query(Download).filter(
            Download.status.in_(_DONE_STATUSES), Download.notified_complete == False  # noqa: E712
        ).all():
            _alert(settings, "notify_on_complete", "AutoRrent — Download complete",
                   f"✅ {dl.title}")
            dl.notified_complete = True
        db.commit()

        for dl in db.query(Download).filter(
            Download.status.in_(_FAILED_STATUSES), Download.notified_failed == False  # noqa: E712
        ).all():
            # Failed outside this sweep (e.g. surfaced by the Downloads page).
            _fail_download(db, dl, settings, "download failed",
                           "notify_on_failed", "notified_failed")

        check_and_alert(db, settings)

    except Exception as exc:
        logger.error("Download event sweep failed: %s", exc)
    finally:
        db.close()
