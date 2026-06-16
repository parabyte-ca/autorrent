import urllib.parse

TRACKERS = [
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]
TRACKER_PARAMS = "&".join(f"tr={urllib.parse.quote(t)}" for t in TRACKERS)


def quality(title: str) -> str:
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


def fmt_size(b: int) -> str:
    if b <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024  # float division, not integer
    return f"{b:.1f} PB"
