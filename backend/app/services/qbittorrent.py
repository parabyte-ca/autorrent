import re
import urllib.parse

import qbittorrentapi

from ..database import SessionLocal
from ..models import Setting


def _get_settings() -> dict[str, str]:
    db = SessionLocal()
    try:
        return {s.key: s.value for s in db.query(Setting).all()}
    finally:
        db.close()


def _get_client() -> qbittorrentapi.Client:
    s = _get_settings()
    client = qbittorrentapi.Client(
        host=s.get("qbit_host", "localhost"),
        port=int(s.get("qbit_port", "8080")),
        username=s.get("qbit_username", "admin"),
        password=s.get("qbit_password", ""),
    )
    client.auth_log_in()
    return client


def _hash_from_magnet(magnet: str) -> str | None:
    match = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
    return match.group(1).lower() if match else None


def add_torrent(magnet: str, save_path: str, category: str | None = None) -> str | None:
    client = _get_client()
    kwargs: dict = {"urls": magnet, "save_path": save_path}
    if category:
        kwargs["category"] = category
    client.torrents_add(**kwargs)
    return _hash_from_magnet(magnet)


def get_torrent_status(info_hash: str) -> dict | None:
    try:
        client = _get_client()
        torrents = client.torrents_info(torrent_hashes=info_hash)
        if torrents:
            t = torrents[0]
            return {
                "status": t.state,
                "progress": round(t.progress, 4),
                "size": t.size,
                "dlspeed": t.dlspeed,
                "eta": t.eta,
            }
    except Exception:
        pass
    return None


def test_connection() -> tuple[bool, str]:
    try:
        client = _get_client()
        version = client.app_version()
        return True, f"Connected to qBittorrent {version}"
    except Exception as e:
        return False, str(e)
