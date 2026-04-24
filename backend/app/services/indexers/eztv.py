import urllib.parse

import httpx

from .nyaa import TRACKER_PARAMS, _quality

# eztvx.to is the primary domain; eztv.re is an alias that redirects there
EZTV_API = "https://eztvx.to/api/get-torrents"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def search_eztv(query: str) -> list[dict]:
    with httpx.Client(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        r = client.get(EZTV_API, params={"limit": 100, "keywords": query})
        r.raise_for_status()
        data = r.json()

    torrents = data.get("torrents") or []
    results = []

    for item in torrents:
        info_hash = (item.get("hash") or "").lower()
        # 'filename' carries the full torrent name with quality tags
        title = item.get("filename") or item.get("title") or ""
        magnet = item.get("magnet_url") or ""
        seeds = int(item.get("seeds") or 0)
        leeches = int(item.get("peers") or 0)
        # size_bytes comes back as a string from the API
        size_bytes = int(item.get("size_bytes") or 0)

        if not info_hash or not magnet:
            continue

        results.append({
            "title": title,
            "size": _fmt_size(size_bytes),
            "size_bytes": size_bytes,
            "seeds": seeds,
            "leeches": leeches,
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": _quality(title),
            "source": "eztv",
            "url": f"https://eztvx.to/ep/{item.get('id', '')}",
        })

    return results


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"
