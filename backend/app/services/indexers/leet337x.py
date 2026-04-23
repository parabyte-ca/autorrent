import re
import urllib.parse

import httpx

from .nyaa import TRACKER_PARAMS, _quality

BASE = "https://1337x.to"
_MAX_RESULTS = 5


def search_1337x(query: str) -> list[dict]:
    encoded = urllib.parse.quote(query)
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        r = client.get(f"{BASE}/search/{encoded}/1/")
        r.raise_for_status()
        html = r.text

    # Extract result rows: name link, seeds, leeches, size
    rows = re.findall(
        r'<td class="name">.*?<a[^>]+href="(/torrent/[^"]+)"[^>]*>([^<]+)</a>'
        r'.*?<td class="seeds">(\d+)</td>'
        r'\s*<td class="leeches">(\d+)</td>'
        r'\s*<td class="date">[^<]*</td>'
        r'\s*<td class="size"[^>]*>([\d.]+ [KMGT]?B)',
        html,
        re.S,
    )

    results = []
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for path, title, seeds_s, leeches_s, size_s in rows[:_MAX_RESULTS]:
            try:
                detail = client.get(f"{BASE}{path}")
                detail.raise_for_status()
                m = re.search(r'href="(magnet:\?xt=urn:btih:[^"]+)"', detail.text)
                if not m:
                    continue
                magnet = m.group(1)

                hash_m = re.search(r"xt=urn:btih:([0-9a-fA-F]{40})", magnet, re.I)
                if not hash_m:
                    continue
                info_hash = hash_m.group(1).lower()

                title = title.strip()
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
    m = re.match(r"([\d.]+)\s*(TB|GB|MB|KB|B)", s, re.I)
    if not m:
        return 0
    num = float(m.group(1))
    units = {"TB": 10**12, "GB": 10**9, "MB": 10**6, "KB": 10**3, "B": 1}
    return int(num * units.get(m.group(2).upper(), 1))
