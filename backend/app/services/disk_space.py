"""Low disk-space detection and alerting for download paths."""
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_WARNED_KEY = "disk_warned"
_REWARN_AFTER = timedelta(hours=24)
_GB = 1_000_000_000


def threshold_bytes(settings: dict) -> int:
    """Configured low-space threshold in bytes; 0 disables the feature."""
    try:
        gb = float(settings.get("low_disk_warn_gb", "10") or 0)
    except (TypeError, ValueError):
        return 0
    return int(gb * _GB) if gb > 0 else 0


def is_low(free_bytes: int | None, threshold: int) -> bool:
    return bool(threshold) and free_bytes is not None and free_bytes < threshold


def check_and_alert(db, settings: dict) -> list[str]:
    """Alert (at most once per 24h per path) about paths below the threshold.

    Returns the names of the low paths, whether or not an alert was sent.
    """
    from ..models import DownloadPath, Setting
    from .apprise_notify import notify

    threshold = threshold_bytes(settings)
    if not threshold:
        return []

    row = db.query(Setting).filter(Setting.key == _WARNED_KEY).first()
    try:
        warned: dict = json.loads(row.value) if row and row.value else {}
    except (ValueError, TypeError):
        warned = {}

    now = datetime.now(timezone.utc)
    low_names: list[str] = []
    changed = False

    for dp in db.query(DownloadPath).all():
        try:
            free = shutil.disk_usage(dp.path).free
        except Exception:
            continue

        key = str(dp.id)
        if not is_low(free, threshold):
            if key in warned:  # recovered — alert again if it drops later
                del warned[key]
                changed = True
            continue

        low_names.append(dp.name)
        last = warned.get(key)
        try:
            last_dt = datetime.fromisoformat(last) if last else None
        except ValueError:
            last_dt = None
        if last_dt is None or now - last_dt >= _REWARN_AFTER:
            notify(
                settings.get("apprise_url", ""),
                title="AutoRrent — Low disk space",
                body=f"⚠️ {dp.name} ({dp.path}) has only {free / _GB:.1f} GB free "
                     f"(warning threshold {threshold / _GB:.0f} GB).",
            )
            warned[key] = now.isoformat()
            changed = True

    if changed:
        blob = json.dumps(warned)
        if row:
            row.value = blob
        else:
            db.add(Setting(key=_WARNED_KEY, value=blob))
        db.commit()

    return low_names
