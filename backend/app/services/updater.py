"""
Update-check and upgrade-request service.

Release information is cached in the ``settings`` table under the key
``update_release_cache`` as a JSON blob so it survives container restarts
without needing Redis.

Upgrade lifecycle
-----------------
The app communicates with the host helper through a shared folder
(APP_UPGRADE_FOLDER, default /upgrade).  Files in that folder:

  request.json  – written by the app (atomically via os.replace)
  agent.json    – written by the helper (heartbeat)
  status.json   – written by the helper (running / succeeded / failed)
  upgrade.log   – written by the helper (output from update.sh)

Derived states surfaced to the admin UI
----------------------------------------
  None        – no activity
  requested   – request.json exists and is < 1 hour old
  running     – status.json state == "running" and < 3 hours old
  succeeded   – status.json state == "succeeded"
  failed      – status.json state == "failed", OR request > 1 h, OR running > 3 h
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .apprise_notify import notify

logger = logging.getLogger(__name__)

# ── Semver ────────────────────────────────────────────────────────────────────

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_semver(tag: str) -> Optional[tuple[int, int, int]]:
    """Return (major, minor, patch) or None if *tag* is not strict semver."""
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def normalise_version(v: str) -> str:
    """Ensure a version string is x.y.z, padding with .0 as needed."""
    parts = v.strip().lstrip("v").split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3])


def get_current_version() -> str:
    return normalise_version(os.environ.get("APP_VERSION", "2.4"))


# ── Folder helpers ────────────────────────────────────────────────────────────

def _get_folder() -> str:
    return os.environ.get("APP_UPGRADE_FOLDER", "/upgrade")


_MAX_FILE_BYTES = 65_536  # 64 KiB — files larger than this are ignored
_LOG_TAIL_BYTES = 65_536


def _read_json_file(path: str) -> Optional[dict]:
    """Read a JSON object file defensively; return None on any problem."""
    try:
        if not os.path.isfile(path) or os.path.islink(path):
            return None
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            logger.warning("Upgrade folder file too large, ignoring: %s", path)
            return None
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cap(val: object, max_len: int = 200) -> str:
    return str(val)[:max_len] if val is not None else ""


def _parse_ts(val: object) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _read_log_tail(folder: str) -> str:
    path = os.path.join(folder, "upgrade.log")
    try:
        size = os.path.getsize(path)
        with open(path, errors="replace") as f:
            if size > _LOG_TAIL_BYTES:
                f.seek(size - _LOG_TAIL_BYTES)
                f.readline()  # skip partial first line
            return f.read()
    except Exception:
        return ""


# ── Settings cache helpers ────────────────────────────────────────────────────

_CACHE_KEY = "update_release_cache"
_NOTIFIED_KEY = "update_notified_version"


def _load_cache(db) -> dict:
    from ..models import Setting
    row = db.query(Setting).filter(Setting.key == _CACHE_KEY).first()
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(db, data: dict) -> None:
    from ..models import Setting
    row = db.query(Setting).filter(Setting.key == _CACHE_KEY).first()
    blob = json.dumps(data)
    if row:
        row.value = blob
    else:
        db.add(Setting(key=_CACHE_KEY, value=blob))
    db.commit()


# ── Helper connected logic ────────────────────────────────────────────────────

def is_helper_connected(agent: dict) -> bool:
    """
    The helper is connected when agent.json last_seen is within
    max(3, 2 × every_minutes + 1) minutes and not more than 1 minute
    in the future (tolerates clock skew).
    """
    last_seen = _parse_ts(agent.get("last_seen"))
    if not last_seen:
        return False
    every = agent.get("every_minutes", 15)
    if not isinstance(every, (int, float)) or every <= 0:
        every = 15
    now = datetime.now(timezone.utc)
    if last_seen > now + timedelta(minutes=1):
        return False  # clock-skewed heartbeat
    grace_s = max(3, 2 * int(every) + 1) * 60
    return (now - last_seen).total_seconds() <= grace_s


# ── Upgrade state derivation ──────────────────────────────────────────────────

def derive_upgrade_state(folder: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (state, message).

    state is one of: None, "requested", "running", "succeeded", "failed"
    """
    now = datetime.now(timezone.utc)

    status = _read_json_file(os.path.join(folder, "status.json"))
    if status:
        state = _cap(status.get("state"), 20)
        msg = _cap(status.get("message"), 300) or None
        if state == "running":
            started = _parse_ts(status.get("started_at"))
            if started and (now - started).total_seconds() > 3 * 3600:
                return "failed", "The upgrade helper stopped reporting."
            return "running", msg
        if state in ("succeeded", "failed"):
            return state, msg

    request = _read_json_file(os.path.join(folder, "request.json"))
    if request:
        requested_at = _parse_ts(request.get("requested_at"))
        if requested_at and (now - requested_at).total_seconds() > 3600:
            return "failed", "The upgrade helper never picked up this request."
        return "requested", None

    return None, None


# ── Rate limit for "check now" ────────────────────────────────────────────────

_last_manual_check: Optional[datetime] = None
_RATE_LIMIT_S = 60


def check_now_rate_limited() -> bool:
    """Return True if a new check should proceed; False if too soon."""
    global _last_manual_check
    now = datetime.now(timezone.utc)
    if _last_manual_check and (now - _last_manual_check).total_seconds() < _RATE_LIMIT_S:
        return False
    _last_manual_check = now
    return True


# ── Source URL parsing ────────────────────────────────────────────────────────

_DEFAULT_SOURCE = "https://github.com/parabyte-ca/autorrent"
_GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/|$)")


def parse_gh_repo(url: str) -> Optional[tuple[str, str]]:
    m = _GH_RE.search(url)
    return (m.group(1), m.group(2)) if m else None


# ── GitHub release fetch ──────────────────────────────────────────────────────

async def _fetch_latest_release(owner: str, repo: str) -> dict:
    version = get_current_version()
    headers = {
        "User-Agent": f"AutoRrent/{version}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    tag = data.get("tag_name", "")
    parsed = parse_semver(tag)
    if parsed is None:
        raise ValueError(f"Release tag {tag!r} is not strict semver (v1.2.3 or 1.2.3 required)")

    return {
        "version": f"{parsed[0]}.{parsed[1]}.{parsed[2]}",
        "name": _cap(data.get("name") or tag, 200),
        "notes": (data.get("body") or "")[:20_000],
        "url": _cap(data.get("html_url"), 500),
        "published_at": _cap(data.get("published_at"), 50),
    }


# ── Public status builder ─────────────────────────────────────────────────────

def get_status(db, *, include_log: bool = False) -> dict:
    """Build the full status payload for GET /api/admin/updates."""
    folder = _get_folder()
    cache = _load_cache(db)
    current = get_current_version()

    # Helper info from agent.json
    agent = _read_json_file(os.path.join(folder, "agent.json")) or {}
    helper_configured = bool(agent)
    helper_connected = is_helper_connected(agent) if agent else False
    every = agent.get("every_minutes", 15)
    if not isinstance(every, (int, float)) or every <= 0:
        every = 15

    # Latest release from cache
    latest: Optional[dict] = None
    if cache.get("version"):
        latest = {
            "version": _cap(cache.get("version"), 20),
            "name": _cap(cache.get("name"), 200),
            "notes": (cache.get("notes") or "")[:20_000],
            "url": _cap(cache.get("url"), 500),
            "published_at": _cap(cache.get("published_at"), 50),
        }

    update_available = False
    if latest:
        cur_t = parse_semver(current)
        lat_t = parse_semver(latest["version"])
        if cur_t and lat_t:
            update_available = lat_t > cur_t

    upgrade_state, upgrade_message = derive_upgrade_state(folder)
    in_progress = upgrade_state in ("requested", "running")

    result = {
        "current_version": current,
        "latest_release": latest,
        "update_available": update_available,
        "checked_at": _cap(cache.get("checked_at"), 50) or None,
        "check_error": _cap(cache.get("check_error"), 300) or None,
        "helper": {
            "configured": helper_configured,
            "connected": helper_connected,
            "last_seen": _cap(agent.get("last_seen"), 50) or None,
            "every_minutes": int(every),
        },
        "upgrade_state": upgrade_state,
        "upgrade_message": upgrade_message,
        "can_upgrade": update_available and helper_connected and not in_progress,
        "helper_install_cmd": "sudo bash scripts/upgrade-agent.sh --install",
    }

    if include_log:
        result["upgrade_log"] = _read_log_tail(folder)

    return result


# ── Async update check ────────────────────────────────────────────────────────

async def fetch_and_cache(db) -> None:
    """Call GitHub API, update the cache; keep previous release on failure."""
    from ..models import Setting

    s_row = db.query(Setting).filter(Setting.key == "app_source_url").first()
    source_url = (s_row.value if s_row and s_row.value else None) or _DEFAULT_SOURCE

    parsed = parse_gh_repo(source_url)
    if not parsed:
        raise ValueError(f"Cannot parse GitHub owner/repo from URL: {source_url!r}")
    owner, repo = parsed

    cache = _load_cache(db)
    now = datetime.now(timezone.utc).isoformat()

    try:
        release = await _fetch_latest_release(owner, repo)
        new_cache = {**release, "checked_at": now, "check_error": None}
        _save_cache(db, new_cache)
        _maybe_notify(db, release["version"])

    except Exception as exc:
        err = str(exc)[:300]
        logger.error("Update check failed: %s", err)
        _save_cache(db, {**cache, "checked_at": now, "check_error": err})


def _maybe_notify(db, version: str) -> None:
    """Send an Apprise notification for a new release — once per version."""
    from ..models import Setting

    current_t = parse_semver(get_current_version())
    new_t = parse_semver(version)
    if not (current_t and new_t and new_t > current_t):
        return

    row = db.query(Setting).filter(Setting.key == _NOTIFIED_KEY).first()
    if row and row.value == version:
        return

    settings = {s.key: s.value for s in db.query(Setting).all()}
    apprise_url = settings.get("apprise_url", "")
    if apprise_url:
        notify(
            apprise_url,
            title="AutoRrent — New Release Available",
            body=f"Version {version} is available. Open Settings → Updates to upgrade.",
        )

    if row:
        row.value = version
    else:
        db.add(Setting(key=_NOTIFIED_KEY, value=version))
    db.commit()


# ── Scheduler job (sync wrapper) ──────────────────────────────────────────────

def daily_update_check() -> None:
    """APScheduler-safe sync wrapper for the daily release check."""
    if os.environ.get("APP_UPDATE_CHECK", "true").lower() == "false":
        logger.debug("Update check disabled by APP_UPDATE_CHECK=false.")
        return

    from ..database import SessionLocal
    db = SessionLocal()
    try:
        asyncio.run(fetch_and_cache(db))
        logger.info("Daily update check complete.")
    except Exception as exc:
        logger.error("Daily update check failed: %s", exc)
    finally:
        db.close()


# ── Upgrade request ───────────────────────────────────────────────────────────

def request_upgrade(db, version: str, requested_by: str) -> None:
    """
    Write request.json atomically to the upgrade folder after all guard checks.

    Raises ValueError with a user-facing message on any validation failure.
    Raises OSError if the folder is not writable (with install instructions).
    """
    folder = _get_folder()
    cache = _load_cache(db)

    if not cache.get("version"):
        raise ValueError("No release information cached — run a check first.")

    latest_version = cache["version"]
    if version != latest_version:
        raise ValueError(
            f"Version {version!r} does not match the latest cached release {latest_version!r}."
        )

    current = get_current_version()
    cur_t = parse_semver(current)
    new_t = parse_semver(version)
    if not (cur_t and new_t and new_t > cur_t):
        raise ValueError(
            f"Version {version} is not newer than the running version {current}."
        )

    state, _ = derive_upgrade_state(folder)
    if state in ("requested", "running"):
        raise ValueError("An upgrade is already in progress — please wait.")

    agent = _read_json_file(os.path.join(folder, "agent.json")) or {}
    if not is_helper_connected(agent):
        raise ValueError(
            "The upgrade helper is not connected. "
            "Run: sudo bash scripts/upgrade-agent.sh --install"
        )

    request_data = {
        "id": str(_uuid_mod.uuid4()),
        "version": version,
        "requested_by": requested_by[:200],
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(folder, exist_ok=True)
    request_path = os.path.join(folder, "request.json")
    tmp_path = request_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(request_data, f)
        os.replace(tmp_path, request_path)
    except OSError as exc:
        raise OSError(
            f"Could not write to upgrade folder {folder!r}. "
            f"Run: sudo bash scripts/upgrade-agent.sh --install\n({exc})"
        ) from exc

    # Audit notification
    from ..models import Setting
    settings = {s.key: s.value for s in db.query(Setting).all()}
    apprise_url = settings.get("apprise_url", "")
    if apprise_url:
        notify(
            apprise_url,
            title="AutoRrent — Upgrade Requested",
            body=f"Upgrade {current} → {version} requested by {requested_by}.",
        )
    logger.info("Upgrade requested: %s → %s by %s", current, version, requested_by)
