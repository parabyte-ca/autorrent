import logging
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting
from ..schemas import DigestTestRequest, JellyfinTestRequest, PlexTestRequest
from ..services.qbittorrent import test_connection
from ..services.scheduler import reschedule_digest, update_interval

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULTS: dict[str, str] = {
    "qbit_host": "localhost",
    "qbit_port": "8080",
    "qbit_username": "admin",
    "qbit_password": "",
    "qbit_category": "autorrent",
    "scan_interval_minutes": "60",
    "min_seeds": "3",
    "remove_on_complete": "false",
    "jackett_url": "",
    "jackett_api_key": "",
    "apprise_url": "",
    "default_indexer": "all",
    # Plex
    "plex_enabled": "false",
    "plex_url": "",
    "plex_token": "",
    "plex_library_key": "",
    # Jellyfin
    "jellyfin_enabled": "false",
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_library_id": "",
    # Weekly digest
    "digest_enabled":      "false",
    "digest_smtp_host":    "",
    "digest_smtp_port":    "587",
    "digest_smtp_user":    "",
    "digest_smtp_password": "",
    "digest_from_email":   "",
    "digest_recipients":   "",
    "digest_day_of_week":  "mon",
    "digest_hour":         "8",
    "digest_excluded_libs": "",
    # Security
    "ui_password": "",
}

# Keys stored in the DB but never sent to the frontend
_PRIVATE_KEYS = {"session_secret"}


def _load_settings(db: Session) -> dict[str, str]:
    db_settings = {
        s.key: s.value
        for s in db.query(Setting).all()
        if s.key not in _PRIVATE_KEYS
    }
    return {**DEFAULTS, **db_settings}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return _load_settings(db)


@router.put("/settings")
def update_settings(updates: dict, db: Session = Depends(get_db)):
    for key, value in updates.items():
        if key in _PRIVATE_KEYS:
            continue
        setting = db.query(Setting).filter(Setting.key == key).first()
        str_val = str(value) if value is not None else ""
        if setting:
            setting.value = str_val
        else:
            db.add(Setting(key=key, value=str_val))
    db.commit()

    if "scan_interval_minutes" in updates:
        try:
            update_interval(int(updates["scan_interval_minutes"]))
        except Exception:
            pass

    if "digest_day_of_week" in updates or "digest_hour" in updates:
        try:
            loaded = _load_settings(db)
            day  = str(updates.get("digest_day_of_week") or loaded.get("digest_day_of_week") or "mon")
            hour = int(updates.get("digest_hour") or loaded.get("digest_hour") or 8)
            reschedule_digest(day, hour)
        except Exception:
            pass

    return _load_settings(db)


@router.post("/settings/test-qbit")
def test_qbit(db: Session = Depends(get_db)):
    success, message = test_connection()
    return {"success": success, "message": message}


@router.post("/settings/test-plex")
async def test_plex(body: PlexTestRequest):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{body.url}/library/sections",
                params={"X-Plex-Token": body.token},
            )
            if not resp.is_success:
                return {"ok": False, "error": f"HTTP {resp.status_code} — check URL and token"}
            root = ET.fromstring(resp.text)
            libraries = [
                {
                    "key": d.get("key", ""),
                    "title": d.get("title", ""),
                    "type": d.get("type", ""),
                }
                for d in root.findall(".//Directory")
                if d.get("key")
            ]
            return {"ok": True, "libraries": libraries}
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused — check host and port"}
    except ET.ParseError as e:
        return {"ok": False, "error": f"Unexpected response format: {e}"}
    except Exception as e:
        logger.error("Plex test connection error: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/settings/test-digest")
def test_digest(body: DigestTestRequest, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    from ..services.plex_digest import fetch_digest_sections, render_html, send_email

    s = _load_settings(db)

    # Recipients come from the request body (current form state, not necessarily saved yet)
    recipients_raw = body.digest_recipients or ""
    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        return {"ok": False, "error": "No recipients configured"}

    plex_url   = body.plex_url or s.get("plex_url", "")
    plex_token = body.plex_token or s.get("plex_token", "")
    if not plex_url or not plex_token:
        return {"ok": False, "error": "Plex URL and token are required — fill in the Plex section above"}

    smtp_host = body.digest_smtp_host
    if not smtp_host:
        return {"ok": False, "error": "SMTP host not configured"}

    excluded_raw = body.digest_excluded_libs or ""
    excluded_libs = frozenset(
        x.strip().lower() for x in excluded_raw.replace("\n", ",").split(",") if x.strip()
    )

    try:
        sections = fetch_digest_sections(plex_url, plex_token, excluded_libs=excluded_libs)
        now = datetime.now(timezone.utc)
        week_label = f"Test — {now.strftime('%B %d, %Y')}"
        html = render_html(sections, week_label)
        smtp_cfg = {
            "host":       smtp_host,
            "port":       body.digest_smtp_port or "587",
            "user":       body.digest_smtp_user,
            "password":   body.digest_smtp_password,
            "from_email": body.digest_from_email,
        }
        send_email(smtp_cfg, recipients, html, week_label)
        return {"ok": True, "message": f"Digest sent to {len(recipients)} recipient(s)"}
    except Exception as e:
        logger.error("test-digest failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/settings/test-jellyfin")
async def test_jellyfin(body: JellyfinTestRequest):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{body.url}/System/Info",
                params={"api_key": body.api_key},
            )
            if not resp.is_success:
                return {"ok": False, "error": f"HTTP {resp.status_code} — check URL and API key"}
            data = resp.json()
            return {
                "ok": True,
                "server_name": data.get("ServerName", ""),
                "version": data.get("Version", ""),
            }
    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused — check host and port"}
    except Exception as e:
        logger.error("Jellyfin test connection error: %s", e)
        return {"ok": False, "error": str(e)}
