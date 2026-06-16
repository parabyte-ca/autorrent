import httpx

from .utils import quality


def search_jackett(query: str, jackett_url: str, api_key: str) -> list[dict]:
    base = jackett_url.rstrip("/")
    is_prowlarr = False

    # Try Prowlarr API first (/api/v1/search), fall back to Jackett (/api/v2.0/...)
    with httpx.Client(timeout=30) as client:
        prowlarr_url = f"{base}/api/v1/search"
        r = client.get(prowlarr_url, params={"query": query, "type": "search", "apikey": api_key})
        if r.status_code == 404:
            # Jackett fallback
            jackett_api_url = f"{base}/api/v2.0/indexers/all/results"
            r = client.get(jackett_api_url, params={"apikey": api_key, "Query": query})
        else:
            is_prowlarr = True
        r.raise_for_status()
        data = r.json()

    # Prowlarr returns a plain array; Jackett wraps results in {"Results": [...]}
    items = data if isinstance(data, list) else data.get("Results", [])

    prefix = "prowlarr" if is_prowlarr else "jackett"
    results = []
    for item in items:
        # Prowlarr uses magnetUrl/downloadUrl; Jackett uses MagnetUri/Link
        magnet = item.get("magnetUrl") or item.get("MagnetUri") or item.get("downloadUrl") or item.get("Link", "")
        if not magnet or not magnet.startswith("magnet:"):
            continue

        size_bytes = int(item.get("size") or item.get("Size") or 0)
        title = item.get("title") or item.get("Title", "")
        info_hash = (item.get("infoHash") or item.get("InfoHash") or "").lower()
        seeds = int(item.get("seeders") or item.get("Seeders") or 0)
        leeches = int(item.get("leechers") or item.get("Peers") or 0)
        indexer = item.get("indexer") or item.get("Tracker") or "unknown"

        results.append({
            "title": title,
            "size": f"{size_bytes / (1024 ** 3):.2f} GB" if size_bytes else "Unknown",
            "size_bytes": size_bytes,
            "seeds": seeds,
            "leeches": leeches,
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": quality(title),
            "source": f"{prefix}/{indexer}",
            "url": item.get("infoUrl") or item.get("Details", ""),
        })

    return results
