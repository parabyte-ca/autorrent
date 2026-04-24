import re
import urllib.parse

import httpx

from .nyaa import TRACKER_PARAMS, _quality

BASE = "https://1337x.to"
_MAX_RESULTS = 5

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://1337x.to/",
}


def search_1337x(query: str) -> list[dict]:
    encoded = urllib.parse.quote(query)
    with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as client:
        try:
            r = client.get(f"{BASE}/search/{encoded}/1/")
            r.raise_for_status()
        except Exception:
            return []

        html = r.text

        # Each result row contains: detail link, title, seeds, leeches, size
        rows = re.findall(
            r'href="(/torrent/\d+/[^"]+)"[^>]*>([^<]{3,})</a>'
            r'.*?<td class="seeds">(\d+)</td>'
            r'\s*<td class="leeches">(\d+)</td>'
            r'.*?<td class="size"[^>]*>([\d.,]+\s*[KMGT]?i?B)',
            html,
            re.S,
        )

        results = []
        seen: set[str] = set()

        for path, title, seeds_s, leeches_s, size_s in rows[:_MAX_RESULTS]:
            title = title.strip()
            if not title or path in seen:
                continue
            seen.add(path)

            try:
                detail = client.get(f"{BASE}{path}")
                detail.raise_for_status()
                m = re.search(r'href="(magnet:\?xt=urn:btih:[^"]+)"', detail.text)
                if not m:
                    continue
                magnet = m.group(1)

                hash_m = re.search(r"btih:([0-9a-fA-F]{32,40})", magnet, re.I)
                if not hash_m:
                    continue
                info_hash = hash_m.group(1).lower()

                seeds = int(seeds_s)
                leeches = int(leeches_s)
                size_bytes = _parse_size_str(size_s.strip())

                results.append({
                    "title": title,
                    "size": size_s.strip(),
                    "size_bytes": size_bytes,
                    "seeds": seeds,
                    "leeches": leeches,
                    "magnet": magnet,
                    "info_hash": info_hash,
                    "quality": _quality(title),
                    "source": "1337x",
                    "url": f"{BASE}{path}",
                })
            except Exception:
                continue

    return results


def _parse_size_str(s: str) -> int:
    m = re.match(r"([\d.,]+)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB|B)", s, re.I)
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    units = {
        "TiB": 1 << 40, "GiB": 1 << 30, "MiB": 1 << 20, "KiB": 1 << 10,
        "TB": 10**12, "GB": 10**9, "MB": 10**6, "KB": 10**3, "B": 1,
    }
    return int(num * units.get(m.group(2), 1))
