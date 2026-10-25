import urllib.parse

import httpx

from .utils import quality


def _pick_link(item: dict, title: str, info_hash: str) -> str:
    """Best way to fetch this result: magnet, else .torrent URL, else bare hash.

    A .torrent URL beats a hash-only magnet because private trackers need the
    passkey embedded in the file; a bare hash would never find peers there.
    """
    magnet = item.get("magnetUrl") or item.get("MagnetUri") or ""
    if magnet.startswith("magnet:"):
        return magnet
    guid = item.get("guid") or item.get("Guid") or ""
    if isinstance(guid, str) and guid.startswith("magnet:"):
        return guid
    url = item.get("downloadUrl") or item.get("Link") or ""
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    if info_hash:
        return f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote(title)}"
    return ""


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
        title = item.get("title") or item.get("Title", "")
        info_hash = (item.get("infoHash") or item.get("InfoHash") or "").lower()
        link = _pick_link(item, title, info_hash)
        if not link:
            continue

        size_bytes = int(item.get("size") or item.get("Size") or 0)
        seeds = int(item.get("seeders") or item.get("Seeders") or 0)
        leeches = int(item.get("leechers") or item.get("Peers") or 0)
        indexer = item.get("indexer") or item.get("Tracker") or "unknown"

        results.append({
            "title": title,
            "size": f"{size_bytes / (1024 ** 3):.2f} GB" if size_bytes else "Unknown",
            "size_bytes": size_bytes,
            "seeds": seeds,
            "leeches": leeches,
            # May be a magnet link or a .torrent URL; add_torrent resolves URLs.
            "magnet": link,
            "info_hash": info_hash,
            "quality": quality(title),
            "source": f"{prefix}/{indexer}",
            "url": item.get("infoUrl") or item.get("Details", ""),
        })

    return results


def probe_jackett(base_url: str, api_key: str) -> dict:
    """Probe a Prowlarr/Jackett instance; returns {ok, kind, version, indexers, error}."""
    base = base_url.rstrip("/")
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{base}/api/v1/system/status", headers={"X-Api-Key": api_key})
            if r.status_code == 401:
                return {"ok": False, "error": "Invalid API key."}
            if r.is_success:
                version = r.json().get("version")
                idx = client.get(f"{base}/api/v1/indexer", headers={"X-Api-Key": api_key})
                idx.raise_for_status()
                enabled = [i for i in idx.json() if i.get("enable", True)]
                return {"ok": True, "kind": "Prowlarr", "version": version,
                        "indexers": len(enabled)}

            r = client.get(f"{base}/api/v2.0/indexers",
                           params={"apikey": api_key, "configured": "true"})
            if r.status_code in (401, 403):
                return {"ok": False, "error": "Invalid API key."}
            r.raise_for_status()
            data = r.json()
            return {"ok": True, "kind": "Jackett", "version": None,
                    "indexers": len(data) if isinstance(data, list) else 0}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach {base}: {type(exc).__name__}"}
    except ValueError:
        return {"ok": False, "error": "Unexpected response — is this a Prowlarr/Jackett URL?"}
