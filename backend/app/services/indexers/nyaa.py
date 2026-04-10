import urllib.parse
import xml.etree.ElementTree as ET

import httpx

TRACKERS = [
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]
TRACKER_PARAMS = "&".join(f"tr={urllib.parse.quote(t)}" for t in TRACKERS)

NYAA_NS = "https://nyaa.si/xmlns/nyaa"


def _parse_size(size_str: str) -> int:
    import re
    m = re.match(r"([\d.]+)\s*(GiB|MiB|KiB|GB|MB|KB|B)", size_str.strip())
    if not m:
        return 0
    num = float(m.group(1))
    units = {"GiB": 1 << 30, "MiB": 1 << 20, "KiB": 1 << 10,
             "GB": 10**9, "MB": 10**6, "KB": 10**3, "B": 1}
    return int(num * units.get(m.group(2), 1))


def _quality(title: str) -> str:
    t = title.lower()
    if "2160p" in t or "4k" in t:
        return "4K"
    if "1080p" in t:
        return "1080p"
    if "720p" in t:
        return "720p"
    if "480p" in t:
        return "480p"
    return "Unknown"


def search_nyaa(query: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        r = client.get(
            "https://nyaa.si/",
            params={"page": "rss", "q": query, "c": "0_0", "f": "0"},
        )
        r.raise_for_status()

    root = ET.fromstring(r.text)
    results = []

    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        guid = item.findtext("guid", "")
        torrent_id = guid.rstrip("/").split("/")[-1]

        info_hash = item.findtext(f"{{{NYAA_NS}}}infoHash", "").lower()
        size_str = item.findtext(f"{{{NYAA_NS}}}size", "0 B")
        seeds = int(item.findtext(f"{{{NYAA_NS}}}seeders", "0") or 0)
        leeches = int(item.findtext(f"{{{NYAA_NS}}}leechers", "0") or 0)

        if not info_hash:
            continue

        magnet = (
            f"magnet:?xt=urn:btih:{info_hash}"
            f"&dn={urllib.parse.quote(title)}"
            f"&{TRACKER_PARAMS}"
        )

        results.append({
            "title": title,
            "size": size_str,
            "size_bytes": _parse_size(size_str),
            "seeds": seeds,
            "leeches": leeches,
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": _quality(title),
            "source": "nyaa",
            "url": f"https://nyaa.si/view/{torrent_id}",
        })

    return results
