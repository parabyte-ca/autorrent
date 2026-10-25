"""qBittorrent torrent-state groupings shared by the API and background jobs."""

# States meaning the torrent has finished downloading.
QBITTORRENT_COMPLETE_STATES = {
    "uploading",    # seeding normally
    "stalledUP",    # seeding, no peers
    "checkingUP",   # integrity check after completion
    "forcedUP",     # force-started seeding
    "pausedUP",     # paused after completion
    "queuedUP",     # queued to seed (upload queue full) — download IS complete
    "completed",    # explicitly marked complete by qBittorrent
    "moving",       # being moved to final location — grace-period clock starts once done
}

DOWNLOADING_STATES = {
    "downloading",
    "stalledDL",         # stalled, no peers
    "forcedDL",          # force-started download
    "checkingDL",        # integrity check mid-download
    "metaDL",            # fetching torrent metadata
    "queuedDL",          # waiting in download queue (0% but not stuck in AutoRrent)
    "pausedDL",          # paused by user in qBittorrent
    "allocating",        # allocating disk space before downloading
    "checkingResumeData",  # rechecking on qBittorrent restart
}

ERROR_STATES = {"error", "missingFiles"}
