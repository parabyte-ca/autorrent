# AutoRrent

> A self-hosted torrent manager with a clean, approachable web UI — built for people who just want things to work.

**v1.1** — Now with a collapsible sidebar and light / dark / system theme support.

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

### Downloads
- Live status pulled from qBittorrent — progress bars, download speed, and estimated time remaining
- Separate views for active downloads and history
- Page auto-refreshes every 10 seconds

### NAS Folder Management
- Add your NAS paths once in Settings with a friendly display name (e.g. "TV Shows 4K" → `/mnt/nas/tv/4k`)
- Pick from a dropdown when downloading — no typing paths manually
- Mark any folder as the default

### Notifications
- Optional notifications via [Apprise](https://github.com/caronc/apprise) — supports Telegram, Discord, Slack, email, Pushover, Ntfy, Gotify, and 60+ other services
- Triggered automatically whenever the watchlist downloads a new episode
- Configured with a single URL string in Settings

### UI & Accessibility
- **Collapsible sidebar** — collapse the nav to icons-only to maximise screen space; preference is saved between sessions
- **Theme picker** — choose **Light**, **Dark**, or **System** (follows your OS preference); fully dark-mode throughout

### Self-Hosted & Simple
- Runs in a **single Docker container** — no separate database server, no Redis, no message queue
- SQLite database stored in a local volume — easy to back up, easy to move
- All settings live in the UI — no environment variables or config files required beyond `docker-compose.yml`

---

## Quick Start

**Requirements:** Docker and Docker Compose on your server.

```bash
git clone https://github.com/parabyte-ca/autorrent.git
cd autorrent
docker compose up -d
```

Open **http://your-server-ip:8180** in your browser.

> Port `8180` is used by default. If you need a different port, change the left-hand side of `"8180:8000"` in `docker-compose.yml`.

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

All downloads added through AutoRrent appear here. Active downloads show a live progress bar, speed, and ETA. The history section tracks everything that's been grabbed. The page refreshes automatically every 10 seconds.

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
| Jackett / Prowlarr URL | — | e.g. `http://192.168.1.100:9117` |
| Jackett / Prowlarr API key | — | Found in your Jackett/Prowlarr dashboard |
| Apprise URL | — | Notification endpoint — leave blank to disable |

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
npm run dev   # runs on http://localhost:5173, proxies /api to :8000
```

The Vite dev server proxies all `/api` requests to the FastAPI backend, so hot-reload works for both sides independently.

### Project Structure

```
autorrent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, lifespan, static file serving
│   │   ├── database.py          # SQLAlchemy + SQLite setup
│   │   ├── models.py            # Database models
│   │   ├── schemas.py           # Pydantic request/response schemas
│   │   ├── routers/             # API route handlers
│   │   └── services/
│   │       ├── qbittorrent.py   # qBittorrent API wrapper
│   │       ├── scheduler.py     # APScheduler watchlist scanner
│   │       ├── apprise_notify.py
│   │       └── indexers/        # NYAA, TPB, Jackett search + adult filter
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/               # Search, Watchlist, Downloads, Settings
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
