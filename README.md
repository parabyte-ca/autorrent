# AutoRrent

A self-hosted torrent auto-downloader with a clean web interface! Search for torrents, send them straight to qBittorrent, and set up a watchlist that automatically downloads new TV episodes as they appear.

**v2.2** — GitHub Actions CI/CD pipeline: every push to `main` now automatically builds and publishes a Docker image to GitHub Container Registry (`ghcr.io/parabyte-ca/autorrent:latest`). Updating a running instance is now a single command: `./update.sh`. Version tags like `v1.2.3` produce versioned image tags automatically. Build caching keeps repeat builds fast.

**v2.1** — Completion status accuracy and qBittorrent auto-cleanup: AutoRrent now recognises all seven qBittorrent completion states (`uploading`, `stalledUP`, `checkingUP`, `forcedUP`, `pausedUP`, `completed`, `moving`) so downloads no longer get stuck at "Downloading" after finishing. Once a torrent has been in a complete state for 60 seconds, AutoRrent automatically triggers your Plex/Jellyfin library refresh then removes the torrent entry from qBittorrent (files are always kept). Removal failures are retried on the next poll cycle. Duplicate detection added: manual downloads return a warning if the same torrent has been downloaded before; the watchlist scanner skips duplicates silently.

**v2.0** — Show-ended detection via TVMaze: AutoRrent now checks every active watchlist item against the TVMaze API on a weekly schedule, automatically pauses items when a show has ended, and notifies you via Apprise. Shows that are still running are left untouched. Status badges appear on each watchlist card, and you can override the auto-pause or trigger an immediate status check from the UI.

**v1.3** — Global download history log with search, filtering, CSV export, and per-entry deletion. Backup and restore the full database from Settings. Plex and Jellyfin library refresh on download completion, Docker healthcheck, and a live system status indicator.

AutoRrent connects to your existing [qBittorrent](https://www.qbittorrent.org/) instance and gives it a friendlier face. Search across multiple torrent indexers, send downloads straight to qBittorrent with a folder picker, and set up a watchlist that tracks TV shows and automatically grabs new episodes the moment they appear — no manual checking required.

Everything is configured through the UI. No config files, no command line, no YAML.

---

## Features

### Search
- Search [NYAA.si](https://nyaa.si), [The Pirate Bay](https://thepiratebay.org), and optionally [Jackett](https://github.com/Jackett/Jackett) or [Prowlarr](https://github.com/Prowlarr/Prowlarr) — all from a single search bar
- Filter results by **quality** (4K, 1080p, 720p, 480p) and **codec** (x265, x264, AV1), with x265 as the default
- **Adult content filter** — enabled by default; strips pornographic results using TPB's XXX category flags and a keyword blocklist across all indexers. Toggle it on or off from the search toolbar
- Results show seed count, file size, quality badge, and source — sorted by seeds
- One-click **Download** with a folder picker, or **Add to Watchlist** to start tracking a show

### Watchlist
- Track TV shows by season and episode — AutoRrent searches for the next episode and downloads it automatically
- Set a preferred **quality** and **codec** per show
- Choose which NAS folder each show downloads to
- Enable or disable tracking per show without deleting it
- **Scan Now** button for an immediate check on any item, or scan all at once
- **Show-ended detection** — every Sunday at 03:00 UTC AutoRrent checks each active item against the [TVMaze](https://www.tvmaze.com/) public API. If a show has ended, the item is auto-paused and you receive an Apprise notification. A coloured status badge (Running / Ended / TBD / In Dev / Unknown) appears on every card, along with how long ago the status was last checked. Hit the magnifying-glass button on any card for an immediate check; use "Resume anyway" to re-enable a paused show and set an override so the auto-pause won't fire again

### Downloads
- Live status pulled from qBittorrent — progress bars, download speed, and estimated time remaining
- Separate views for active downloads and history
- Page auto-refreshes every 10 seconds

### NAS Folder Management
- Add your NAS paths once in Settings with a friendly display name (e.g. "TV Shows 4K" → `/mnt/nas/tv/4k`)
- Pick from a dropdown when downloading — no typing paths manually
- Mark any folder as the default

### Media Server Integration
- **Plex** — optionally trigger a Plex library refresh whenever a download completes. Configure your Plex URL and token in Settings; use the built-in "Test connection" button to discover your libraries and choose which one to refresh (or refresh all)
- **Jellyfin** — optionally trigger a Jellyfin library refresh on completion. Enter your Jellyfin URL and API key; target a specific library by ID or leave blank to refresh everything
- Both services are independent — configure one, both, or neither
- Refreshes fire concurrently so neither service blocks the other

### Notifications
- Optional notifications via [Apprise](https://github.com/caronc/apprise) — supports Telegram, Discord, Slack, email, Pushover, Ntfy, Gotify, and 60+ other services
- Triggered automatically whenever the watchlist downloads a new episode
- Configured with a single URL string in Settings

### UI & Accessibility
- **Collapsible sidebar** — collapse the nav to icons-only to maximise screen space; preference is saved between sessions
- **Theme picker** — choose **Light**, **Dark**, or **System** (follows your OS preference); fully dark-mode throughout
- **System status bar** — the Settings page shows a live green/amber/grey indicator with uptime and version, auto-refreshing every 30 seconds

### Backup & Restore
- **Export** a ZIP from Settings containing your SQLite database, all settings, and a metadata file — one click, no command line
- **Restore** by uploading a backup ZIP; AutoRrent atomically swaps the database with zero data loss
- The scheduler is paused for the duration of the restore and resumes automatically

### Self-Hosted & Simple
- Runs in a **single Docker container** — no separate database server, no Redis, no message queue
- SQLite database stored in a local volume — easy to back up, easy to move
- All settings live in the UI — no environment variables or config files required beyond `docker-compose.yml`
- Built-in **Docker healthcheck** — `docker ps` and Compose show container health; monitoring tools can poll `GET /health`

---

## Quick Start

**Requirements:** Docker and Docker Compose on your server.

```bash
git clone https://github.com/parabyte-ca/autorrent.git
cd autorrent
docker compose up -d
```

`docker compose up -d` pulls the pre-built image from GitHub Container Registry and starts the container. No local build step required.

Open **http://your-server-ip:8180** in your browser.

> Port `8180` is used by default. If you need a different port, change the left-hand side of `"8180:8000"` in `docker-compose.yml`.

---

## Updating AutoRrent

AutoRrent is automatically built and published to GitHub Container Registry on every push to `main`.

To update your running instance:

```bash
cd /path/to/autorrent
./update.sh
```

Or manually:

```bash
docker pull ghcr.io/parabyte-ca/autorrent:latest
docker compose up -d
```

Your data is stored in `./data/` and is not affected by updates.

> **First-time setup:** After the first successful GitHub Actions build, the package visibility must be set to **Public** so the image can be pulled without authentication. Go to `https://github.com/parabyte-ca?tab=packages`, find the `autorrent` package, and set its visibility to Public.

---

## First-Time Setup

Go to **Settings** after the app starts and work through these steps:

### 1. Connect to qBittorrent

| Field | Example |
|-------|---------|
| Host | `192.168.1.100` |
| Port | `8080` |
| Username | `admin` |
| Password | `your-password` |

Click **Test connection** — you should see the qBittorrent version if everything is correct.

### 2. Add Download Folders

Add the paths that you want to download to. These must be paths **as qBittorrent sees them** — i.e. the path inside its container or on the machine where it runs, not your local machine.

| Display name | Linux path |
|---|---|
| TV Shows | `/mnt/nas/tv` |
| Movies | `/mnt/nas/movies` |
| Anime | `/mnt/nas/anime` |

Set one as the **default** so it's pre-selected when you download something without specifying a folder.

### 3. (Optional) Connect Jackett or Prowlarr

AutoRrent ships with NYAA and TPB built in. If you already run [Jackett](https://github.com/Jackett/Jackett) or [Prowlarr](https://github.com/Prowlarr/Prowlarr), paste in the URL and API key to add support for hundreds of additional indexers.

### 4. (Optional) Set Up Notifications

Paste an [Apprise URL](https://github.com/caronc/apprise#supported-notifications) into the Notifications field. Common examples:

| Service | URL format |
|---|---|
| Telegram | `tgram://BotToken/ChatID` |
| Discord | `discord://WebhookID/WebhookToken` |
| Slack | `slack://TokenA/TokenB/TokenC/Channel` |
| Ntfy | `ntfy://your-topic` |
| Email | `mailto://user:pass@gmail.com` |

### 5. (Optional) Connect Plex or Jellyfin

Enable the **Plex** or **Jellyfin** section in Settings and fill in your server details. Click **Test connection** to verify — Plex will also populate a library dropdown so you can target a specific section.

Once enabled, AutoRrent automatically pings your media server to trigger a library scan each time a torrent finishes downloading.

---

## Usage

### Searching and Downloading

1. Go to **Search** and type a show name, movie, or episode (e.g. `Breaking Bad S03E01`)
2. Adjust the **Quality**, **Codec**, and **Source** filters as needed — or leave them at their defaults
3. Click **Download** on a result, pick a destination folder, and confirm
4. To auto-track a show going forward, click **Watchlist** instead

### Watchlist

1. Go to **Watchlist** → **Add show**
2. Enter the show title, the search query (what to search for), your preferred quality and codec, and the season/episode to start from
3. AutoRrent will automatically search for and download the next episode on each scan cycle (default: every 60 minutes)
4. Hit **Scan now** on a card to check immediately without waiting for the next cycle
5. Use the toggle on each card to pause or resume tracking at any time

### Downloads

All downloads added through AutoRrent appear here. Active downloads show a live progress bar, speed, and ETA. The page refreshes automatically every 10 seconds.

### History

Every torrent sent to qBittorrent — whether triggered manually from the Search page or automatically by the watchlist scanner — is recorded here. Filter by name, source, or status; export the full log as CSV; or delete individual entries. A "Clear all" button wipes the history without affecting qBittorrent or your files.

---

## NAS / Docker Path Setup

AutoRrent tells qBittorrent where to save files. The path you enter must be valid **from qBittorrent's perspective** — if qBittorrent is running in Docker, use the path inside that container.

To mount your NAS into the AutoRrent container (useful for path validation or future features), uncomment the relevant line in `docker-compose.yml`:

```yaml
volumes:
  - ./data:/app/data
  - /mnt/nas:/mnt/nas   # uncomment to mount NAS into the container
```

---

## Configuration Reference

All settings are managed through the Settings page. The table below documents each option.

| Setting | Default | Description |
|---|---|---|
| qBittorrent host | `localhost` | IP or hostname of your qBittorrent instance |
| qBittorrent port | `8080` | Port qBittorrent's Web UI is running on |
| Username / Password | `admin` / — | qBittorrent Web UI credentials |
| Category | `autorrent` | qBittorrent category label applied to all AutoRrent downloads |
| Scan interval | `60` min | How often the watchlist scanner runs |
| Minimum seeds | `3` | Results with fewer seeds than this are ignored |
| Remove on complete | off | Remove the torrent entry from qBittorrent when seeding begins (files are kept) |
| Jackett / Prowlarr URL | — | e.g. `http://192.168.1.100:9117` |
| Jackett / Prowlarr API key | — | Found in your Jackett/Prowlarr dashboard |
| Plex URL | — | e.g. `http://192.168.1.100:32400` |
| Plex token | — | Your X-Plex-Token — see Settings for a link to Plex's docs |
| Plex library | All | Specific library to refresh, or all libraries |
| Jellyfin URL | — | e.g. `http://192.168.1.100:8096` |
| Jellyfin API key | — | Generated in Jellyfin → Dashboard → API Keys |
| Jellyfin library ID | — | ItemId of the library to refresh; blank refreshes all |
| Apprise URL | — | Notification endpoint — leave blank to disable |

---

## Health Endpoint

`GET /health` returns a JSON status object and is always HTTP 200:

```json
{ "status": "ok", "db_ok": true, "uptime_seconds": 3600, "version": "dev" }
```

`status` is `"ok"` when the database is reachable, `"degraded"` otherwise. Useful for uptime monitors, reverse proxies, and the Docker healthcheck that ships with the image.

The `APP_VERSION` environment variable sets the `version` field — useful when deploying tagged images:

```yaml
environment:
  - APP_VERSION=2.2.0
```

---

## Supported Indexers

| Indexer | Built-in | Notes |
|---|---|---|
| NYAA.si | ✓ | Great for anime; uses the public RSS API |
| The Pirate Bay | ✓ | General content; uses the apibay.org API |
| Jackett | Optional | Aggregates 100+ indexers; requires a running Jackett instance |
| Prowlarr | Optional | Modern Jackett alternative with a compatible API |

---

## Development

**Requirements:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATABASE_URL=sqlite:///./data/autorrent.db uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # runs on http://localhost:5173, proxies /api and /health to :8000
```

The Vite dev server proxies `/api` and `/health` to the FastAPI backend, so hot-reload works for both sides independently.

```bash
# Run backend tests
cd backend
python -m pytest tests/ -v
```

### Project Structure

```
autorrent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, lifespan, static file serving
│   │   ├── database.py          # SQLAlchemy + SQLite setup
│   │   ├── models.py            # Database models
│   │   ├── schemas.py           # Pydantic request/response schemas
│   │   ├── routers/             # API route handlers (health, search, watchlist, history, backup…)
│   │   └── services/
│   │       ├── qbittorrent.py   # qBittorrent API wrapper
│   │       ├── scheduler.py     # APScheduler: watchlist scan + show-status check
│   │       ├── tvmaze.py        # TVMaze API client (show-ended detection)
│   │       ├── media_servers.py # Plex + Jellyfin library refresh
│   │       ├── apprise_notify.py
│   │       └── indexers/        # NYAA, TPB, Jackett search + adult filter
│   ├── tests/                   # pytest unit tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/               # Search, Watchlist, Downloads, History, Settings
│       ├── components/          # Layout, sidebar
│       └── api/client.ts        # Typed fetch wrapper
├── Dockerfile                   # Multi-stage: Node build → Python runtime
└── docker-compose.yml
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy, SQLite |
| Scheduling | APScheduler (in-process, no Redis needed) |
| Torrent client | qbittorrentapi |
| HTTP / scraping | httpx |
| Notifications | Apprise |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS (dark mode) |
| Icons | Lucide React |
| Deployment | Docker (multi-stage build), Docker Compose |

---

## Contributing

Bug reports and pull requests are welcome. Please open an issue first for any significant changes so we can discuss the approach before you invest time in it.

---

## License

MIT
