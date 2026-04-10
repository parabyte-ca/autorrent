import httpx

from .nyaa import _quality


def search_jackett(query: str, jackett_url: str, api_key: str) -> list[dict]:
    url = f"{jackett_url.rstrip('/')}/api/v2.0/indexers/all/results"

    with httpx.Client(timeout=30) as client:
        r = client.get(url, params={"apikey": api_key, "Query": query})
        r.raise_for_status()
        data = r.json()

    results = []
    for item in data.get("Results", []):
        magnet = item.get("MagnetUri") or item.get("Link", "")
        if not magnet:
            continue

        size_bytes = int(item.get("Size", 0))
        title = item.get("Title", "")
        info_hash = (item.get("InfoHash") or "").lower()

        results.append({
            "title": title,
            "size": f"{size_bytes / (1024 ** 3):.2f} GB" if size_bytes else "Unknown",
            "size_bytes": size_bytes,
            "seeds": int(item.get("Seeders", 0)),
            "leeches": int(item.get("Peers", 0)),
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": _quality(title),
            "source": f"jackett/{item.get('Tracker', 'unknown')}",
            "url": item.get("Details", ""),
        })

    return results
