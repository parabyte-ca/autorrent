import logging
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting
from ..schemas import JellyfinTestRequest, PlexTestRequest
from ..services.qbittorrent import test_connection
from ..services.scheduler import update_interval

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
}


def _load_settings(db: Session) -> dict[str, str]:
    db_settings = {s.key: s.value for s in db.query(Setting).all()}
    return {**DEFAULTS, **db_settings}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return _load_settings(db)


@router.put("/settings")
def update_settings(updates: dict, db: Session = Depends(get_db)):
    for key, value in updates.items():
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
