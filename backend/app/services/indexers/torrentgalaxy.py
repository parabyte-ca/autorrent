import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from .nyaa import TRACKER_PARAMS, _quality, _parse_size

TGX_RSS = "https://torrentgalaxy.to/rss.php"

_TGX_NS = "https://torrentgalaxy.to"


def search_torrentgalaxy(query: str) -> list[dict]:
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        r = client.get(TGX_RSS, params={"q": query})
        r.raise_for_status()

    root = ET.fromstring(r.text)
    results = []

    for item in root.findall(".//item"):
        title = item.findtext("title") or ""

        # Magnet link — may be in <link>, <comments>, or a custom element
        magnet = ""
        for tag in ("link", f"{{{_TGX_NS}}}magnet"):
            val = item.findtext(tag) or ""
            if val.startswith("magnet:"):
                magnet = val
                break
        # Fallback: scan all child text for a magnet URI
        if not magnet:
            for child in item:
                text = (child.text or "") + (child.get("url") or "") + (child.get("href") or "")
                if "magnet:?xt=urn:btih:" in text:
                    m = re.search(r"magnet:\?xt=urn:btih:[^&\s\"'<>]+[^\s\"'<>]*", text)
                    if m:
                        magnet = m.group(0)
                        break

        if not magnet:
            continue

        # Extract info_hash from magnet
        m = re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", magnet)
        if not m:
            continue
        info_hash = m.group(1).lower()

        # Seeds/leeches from custom namespace or description
        seeds = _extract_int(item, "seeders", _TGX_NS)
        leeches = _extract_int(item, "leechers", _TGX_NS)

        # Size
        size_str = item.findtext(f"{{{_TGX_NS}}}size") or ""
        size_bytes = _parse_size(size_str) if size_str else 0

        url = item.findtext("comments") or item.findtext("guid") or ""

        results.append({
            "title": title,
            "size": size_str or _fmt_size(size_bytes),
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


def _extract_int(item: ET.Element, tag: str, ns: str) -> int:
    val = item.findtext(f"{{{ns}}}{tag}") or item.findtext(tag) or "0"
    try:
        return int(val)
    except ValueError:
        return 0


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} PB"
