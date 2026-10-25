"""Turn a .torrent download URL into a magnet link (and its info-hash).

Indexer proxies such as Prowlarr/Jackett often return only a download URL for
private trackers.  qBittorrent can fetch URLs itself, but then AutoRrent would
not know the info-hash, which duplicate detection, status tracking and the
retry queue all rely on.  Resolving to a magnet up front keeps one code path.
"""
import hashlib
import re
import urllib.parse

import httpx

MAX_TORRENT_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5
_BTIH = re.compile(r"btih:([a-fA-F0-9]{40,64})", re.IGNORECASE)


def magnet_hash(magnet: str) -> str | None:
    m = _BTIH.search(magnet)
    return m.group(1).lower() if m else None


def _decode(data: bytes, i: int):
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _decode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _decode(data, i)
            v, i = _decode(data, i)
            out[k] = v
        return out, i + 1
    if c.isdigit():
        j = data.index(b":", i)
        n = int(data[i:j])
        return data[j + 1:j + 1 + n], j + 1 + n
    raise ValueError("invalid bencode")


def torrent_to_magnet(data: bytes) -> tuple[str, str]:
    """Return (magnet, info_hash) for the raw bytes of a .torrent file."""
    if data[:1] != b"d":
        raise ValueError("not a torrent file")

    # Walk the top-level dict so we can hash the *raw* bytes of the info dict.
    i = 1
    info_raw: bytes | None = None
    meta: dict = {}
    while data[i:i + 1] != b"e":
        key, i = _decode(data, i)
        start = i
        value, i = _decode(data, i)
        if key == b"info":
            info_raw = data[start:i]
        meta[key] = value
    if info_raw is None:
        raise ValueError("torrent has no info dictionary")

    info_hash = hashlib.sha1(info_raw).hexdigest()
    info = meta[b"info"]
    name = info.get(b"name", b"").decode("utf-8", "replace") if isinstance(info, dict) else ""

    trackers: list[str] = []
    announce = meta.get(b"announce")
    if isinstance(announce, bytes):
        trackers.append(announce.decode("utf-8", "replace"))
    for tier in meta.get(b"announce-list", []) or []:
        for t in tier if isinstance(tier, list) else [tier]:
            if isinstance(t, bytes):
                url = t.decode("utf-8", "replace")
                if url not in trackers:
                    trackers.append(url)

    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    if name:
        magnet += "&dn=" + urllib.parse.quote(name)
    for t in trackers:
        magnet += "&tr=" + urllib.parse.quote(t, safe="")
    return magnet, info_hash


def url_to_magnet(url: str) -> tuple[str, str | None]:
    """Resolve a .torrent URL (or a redirect to a magnet) to (magnet, info_hash)."""
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            resp = client.get(url)
            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if location.lower().startswith("magnet:"):
                    return location, magnet_hash(location)
                url = str(httpx.URL(url).join(location))
                continue
            resp.raise_for_status()
            if len(resp.content) > MAX_TORRENT_BYTES:
                raise ValueError("torrent file is unexpectedly large")
            return torrent_to_magnet(resp.content)
    raise ValueError("too many redirects resolving torrent URL")
