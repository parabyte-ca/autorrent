import urllib.parse

import httpx

from .nyaa import TRACKER_PARAMS, _quality

TPB_API = "https://apibay.org/q.php"


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"


def search_tpb(query: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        r = client.get(TPB_API, params={"q": query, "cat": "0"})
        r.raise_for_status()
        data = r.json()

    if not data or (len(data) == 1 and data[0].get("name") == "No results returned"):
        return []

    results = []
    for item in data:
        info_hash = item.get("info_hash", "").lower()
        name = item.get("name", "")
        size_bytes = int(item.get("size", 0))
        seeds = int(item.get("seeders", 0))
        leeches = int(item.get("leechers", 0))

        if not info_hash:
            continue

        magnet = (
            f"magnet:?xt=urn:btih:{info_hash}"
            f"&dn={urllib.parse.quote(name)}"
            f"&{TRACKER_PARAMS}"
        )

        results.append({
            "title": name,
            "size": _fmt_size(size_bytes),
            "size_bytes": size_bytes,
            "seeds": seeds,
            "leeches": leeches,
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": _quality(name),
            "source": "tpb",
            "url": f"https://thepiratebay.org/description.php?id={item.get('id', '')}",
            "_tpb_category": int(item.get("category", 0)),
        })

    return results
