import urllib.parse

import httpx

from .nyaa import TRACKER_PARAMS

YTS_API = "https://yts.mx/api/v2/list_movies.json"

_YTS_QUALITY_MAP = {
    "2160p": "4K",
    "1080p": "1080p",
    "720p": "720p",
    "480p": "480p",
    "3D": "1080p",
}


def search_yts(query: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        r = client.get(YTS_API, params={"query_term": query, "limit": 50})
        r.raise_for_status()
        data = r.json()

    movies = (data.get("data") or {}).get("movies") or []
    results = []

    for movie in movies:
        title = movie.get("title_long") or movie.get("title") or ""
        slug = movie.get("slug") or ""
        url = f"https://yts.mx/movies/{slug}"

        for torrent in movie.get("torrents") or []:
            info_hash = (torrent.get("hash") or "").lower()
            if not info_hash:
                continue

            quality_raw = torrent.get("quality", "")
            quality = _YTS_QUALITY_MAP.get(quality_raw, "Unknown")
            seeds = int(torrent.get("seeds") or 0)
            leeches = int(torrent.get("peers") or 0)
            size_bytes = int(torrent.get("size_bytes") or 0)
            size = torrent.get("size") or _fmt_size(size_bytes)

            torrent_title = f"{title} [{quality_raw}] [YTS]"
            magnet = (
                f"magnet:?xt=urn:btih:{info_hash}"
                f"&dn={urllib.parse.quote(torrent_title)}"
                f"&{TRACKER_PARAMS}"
            )

            results.append({
                "title": torrent_title,
                "size": size,
                "size_bytes": size_bytes,
                "seeds": seeds,
                "leeches": leeches,
                "magnet": magnet,
                "info_hash": info_hash,
                "quality": quality,
                "source": "yts",
                "url": url,
            })

    return results


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"
