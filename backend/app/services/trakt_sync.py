"""Add shows from a Trakt watchlist to the AutoRrent watchlist."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from . import tvmaze, watchlist_add
from .trakt import TraktError, fetch_watchlist_shows

logger = logging.getLogger(__name__)

# A first sync of a big Trakt watchlist must not add dozens of shows at once.
MAX_NEW_PER_RUN = 10
UNMATCHED_RETRY = timedelta(days=7)
_RESULT_KEY = "trakt_last_result"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _year_ok(show: dict, trakt: dict) -> bool:
    """Guard name-search matches against remakes that share a title."""
    premiered = (show.get("premiered") or "")[:4]
    if trakt.get("year") and premiered.isdigit():
        return abs(int(premiered) - int(trakt["year"])) <= 1
    return _norm(show.get("name") or "") == _norm(trakt["title"])


async def resolve_show(trakt: dict) -> dict | None:
    """Find the TVMaze show for a Trakt entry: by IMDb, then TheTVDB, then name+year."""
    show = None
    if trakt.get("imdb"):
        show = await tvmaze.lookup_show(imdb=trakt["imdb"])
    if show is None and trakt.get("tvdb"):
        show = await tvmaze.lookup_show(thetvdb=trakt["tvdb"])
    if show is None:
        candidate = await tvmaze.search_show(trakt["title"])
        if candidate and _year_ok(candidate, trakt):
            show = candidate
    return show


def _setting(db, key: str, default: str = "") -> str:
    from ..models import Setting

    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value or default) if row else default


def _save_result(db, result: dict) -> None:
    from ..models import Setting

    blob = json.dumps(result)
    row = db.query(Setting).filter(Setting.key == _RESULT_KEY).first()
    if row:
        row.value = blob
    else:
        db.add(Setting(key=_RESULT_KEY, value=blob))
    db.commit()


def last_result(db) -> dict | None:
    try:
        data = json.loads(_setting(db, _RESULT_KEY) or "null")
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _record(db, trakt: dict, status: str, tvmaze_id: int | None, now: datetime) -> None:
    from ..models import TraktSeen

    row = db.query(TraktSeen).filter(TraktSeen.trakt_id == trakt["trakt_id"]).first()
    if row is None:
        row = TraktSeen(trakt_id=trakt["trakt_id"], title=trakt["title"], status=status,
                        tvmaze_id=tvmaze_id, checked_at=now)
        db.add(row)
    else:
        row.status, row.tvmaze_id, row.checked_at = status, tvmaze_id, now
    db.commit()


async def sync(db, now: datetime | None = None) -> dict:
    """Run one sync. Always returns a summary dict and stores it as the last result."""
    from ..models import TraktSeen, WatchlistItem
    from .apprise_notify import notify

    now = now or datetime.now(timezone.utc)
    client_id = _setting(db, "trakt_client_id").strip()
    username = _setting(db, "trakt_username").strip()
    start = _setting(db, "trakt_start", "auto")
    start = start if start in ("next", "beginning") else None

    result = {"ok": False, "error": None, "at": now.isoformat(), "checked": 0,
              "added": [], "existing": 0, "unmatched": [], "deferred": 0}

    try:
        entries = await fetch_watchlist_shows(client_id, username)
    except TraktError as exc:
        result["error"] = str(exc)
        _save_result(db, result)
        return result

    result["checked"] = len(entries)
    seen = {r.trakt_id: r for r in db.query(TraktSeen).all()}
    on_list_ids = {i.tvmaze_id for i in db.query(WatchlistItem).all() if i.tvmaze_id}
    on_list_names = {_norm(i.title) for i in db.query(WatchlistItem).all()}

    for entry in entries:
        prior = seen.get(entry["trakt_id"])
        if prior is not None:
            stale = prior.status == "unmatched" and now - _aware(prior.checked_at) >= UNMATCHED_RETRY
            if not stale:
                continue

        show = await resolve_show(entry)
        if show is None:
            _record(db, entry, "unmatched", None, now)
            result["unmatched"].append(entry["title"])
            continue

        sid = show["id"]
        if sid in on_list_ids or _norm(entry["title"]) in on_list_names:
            _record(db, entry, "existing", sid, now)
            result["existing"] += 1
            continue

        if len(result["added"]) >= MAX_NEW_PER_RUN:
            result["deferred"] += 1   # not recorded: picked up on the next run
            continue

        try:
            item = await watchlist_add.create_from_tvmaze(db, sid, start=start, show=show)
        except watchlist_add.AlreadyOnWatchlist:
            _record(db, entry, "existing", sid, now)
            result["existing"] += 1
            continue
        except watchlist_add.ShowNotFound:
            _record(db, entry, "unmatched", None, now)
            result["unmatched"].append(entry["title"])
            continue
        _record(db, entry, "added", sid, now)
        on_list_ids.add(sid)
        on_list_names.add(_norm(item.title))
        result["added"].append(item.title)

    result["ok"] = True
    _save_result(db, result)

    if result["added"]:
        more = f" ({result['deferred']} more waiting)" if result["deferred"] else ""
        notify(_setting(db, "apprise_url"), title="AutoRrent — Trakt sync",
               body="Added from your Trakt watchlist: " + ", ".join(result["added"]) + more)
    logger.info("Trakt sync: %d on list, %d added, %d existing, %d unmatched, %d deferred",
                result["checked"], len(result["added"]), result["existing"],
                len(result["unmatched"]), result["deferred"])
    return result


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
