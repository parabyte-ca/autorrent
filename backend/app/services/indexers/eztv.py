import re

import httpx

from .nyaa import _quality

EZTV_API = "https://eztvx.to/api/get-torrents"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_STOP = {"a", "an", "the", "and", "or", "of", "in", "is", "to", "at", "for", "on"}


def _query_words(query: str) -> set[str]:
    """Return significant lowercase words from a search query."""
    return {
        w for w in re.sub(r"[^a-z0-9]", " ", query.lower()).split()
        if len(w) > 1 and w not in _STOP
    }


def _title_words(title: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]", " ", title.lower()).split())


def search_eztv(query: str) -> list[dict]:
    with httpx.Client(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        r = client.get(EZTV_API, params={"limit": 100, "keywords": query})
        r.raise_for_status()
        data = r.json()

    # EZTV's `keywords` param is unreliable — the API returns all recent
    # torrents regardless of the query. Build a word set from the query and
    # require ALL significant words to appear in each result title so that
    # completely unrelated shows are dropped.
    required = _query_words(query)

    torrents = data.get("torrents") or []
    results = []

    for item in torrents:
        info_hash = (item.get("hash") or "").lower()
        title = item.get("filename") or item.get("title") or ""
        magnet = item.get("magnet_url") or ""
        seeds = int(item.get("seeds") or 0)
        leeches = int(item.get("peers") or 0)
        size_bytes = int(item.get("size_bytes") or 0)

        if not info_hash or not magnet:
            continue

        # Drop results that don't match every query word.
        if required and not required.issubset(_title_words(title)):
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
