import re
import xml.etree.ElementTree as ET

import httpx

from .nyaa import TRACKER_PARAMS, _quality, _parse_size

TGX_RSS = "https://torrentgalaxy.to/rss.php"

# TorrentGalaxy uses the standard EZRSS torrent namespace
_NS = "http://xmlns.ezrss.it/0.1/"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml",
}


def search_torrentgalaxy(query: str) -> list[dict]:
    with httpx.Client(timeout=15, follow_redirects=True, headers=_HEADERS) as client:
        r = client.get(TGX_RSS, params={"q": query})
        r.raise_for_status()

    root = ET.fromstring(r.text)
    results = []

    for item in root.findall(".//item"):
        title = item.findtext("title") or ""

        # 1. Dedicated <torrent:magnetURI> element (standard EZRSS namespace)
        magnet = item.findtext(f"{{{_NS}}}magnetURI") or ""

        # 2. Fallback: regex scan over all child text and attributes
        if not magnet:
            raw = ET.tostring(item, encoding="unicode")
            m = re.search(r'magnet:\?xt=urn:btih:[^"&<\s]+', raw)
            if m:
                magnet = m.group(0)

        if not magnet:
            continue

        m = re.search(r"xt=urn:btih:([0-9a-fA-F]{32,40})", magnet, re.I)
        if not m:
            continue
        info_hash = m.group(1).lower()

        # Seeds / leeches from EZRSS namespace elements
        seeds = _int(item.findtext(f"{{{_NS}}}seeds"))
        leeches = _int(item.findtext(f"{{{_NS}}}peers"))

        # If namespace tags are absent, try to parse from the description CDATA
        if seeds == 0:
            desc = item.findtext("description") or ""
            sm = re.search(r'[Ss]eeds?[:\s]+(\d+)', desc)
            lm = re.search(r'[Ll]eechers?[:\s]+(\d+)', desc)
            if sm:
                seeds = int(sm.group(1))
            if lm:
                leeches = int(lm.group(1))

        # Size: try namespace element, then description, then torrent:contentLength
        size_str = (
            item.findtext(f"{{{_NS}}}contentLength")
            or item.findtext("size")
            or ""
        )
        size_bytes = 0
        if size_str.isdigit():
            size_bytes = int(size_str)
        else:
            desc = item.findtext("description") or ""
            szm = re.search(r'(\d+(?:\.\d+)?)\s*(TB|GB|MB|KB|B)\b', desc, re.I)
            if szm:
                size_str = szm.group(0)
                size_bytes = _parse_size(size_str)

        url = item.findtext("comments") or item.findtext("link") or ""

        results.append({
            "title": title,
            "size": size_str if not size_str.isdigit() else _fmt_size(size_bytes),
            "size_bytes": size_bytes,
            "seeds": seeds,
            "leeches": leeches,
            "magnet": magnet,
            "info_hash": info_hash,
            "quality": _quality(title),
            "source": "tgx",
            "url": url,
        })

    return results


def _int(val: str | None) -> int:
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"
