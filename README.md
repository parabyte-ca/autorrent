# AutoRrent

A self-hosted torrent auto-downloader with a clean web interface. Search for torrents, send them straight to qBittorrent, and set up a watchlist that automatically downloads new TV episodes as they appear.

Built for non-technical users — configure everything through the UI, no config files required.

---

## Features

- **Search** — search NYAA, The Pirate Bay, and optionally Jackett/Prowlarr from one place
- **One-click download** — pick a destination folder and send directly to qBittorrent
- **Watchlist** — track TV shows by season/episode; new episodes are downloaded automatically on a schedule
- **Download tracking** — live progress bars, speed, and ETA pulled from qBittorrent
- **NAS folder management** — give your Linux paths friendly names (e.g. "TV Shows" → `/mnt/nas/tv`) and pick them from a dropdown
- **Notifications** — optional Apprise support for Telegram, Discord, Slack, email, Pushover, and 60+ more services
- **Self-hosted** — single Docker container, SQLite database, no external dependencies required

---

## Quick Start

**Requirements:** Docker and Docker Compose installed on your server.

```bash
git clone https://github.com/parabyte-ca/autorrent.git
cd autorrent
docker compose up -d
```

Open **http://your-server-ip:8180** in your browser.

> The app will start on port `8180`. Make sure this port is accessible on your network.

---

## First-Time Setup

Once the app is running, go to **Settings** and complete the following steps:

### 1. Connect to qBittorrent

Enter the host (IP address of the machine running qBittorrent), port, username, and password. Click **Test connection** to confirm it works.

| Field | Example |
|-------|---------|
| Host | `192.168.1.100` |
| Port | `8080` |
| Username | `admin` |
| Password | `your-password` |

### 2. Add Download Folders

Add the folders you want to download to. These are paths **on the machine running qBittorrent** (your NAS).

| Display name | Linux path |
|---|---|
| TV Shows | `/mnt/nas/tv` |
| Movies | `/mnt/nas/movies` |
| Anime | `/mnt/nas/anime` |

Mark one as the **default** — it will be pre-selected when you download something.

### 3. (Optional) Connect Jackett or Prowlarr

If you already run [Jackett](https://github.com/Jackett/Jackett) or [Prowlarr](https://github.com/Prowlarr/Prowlarr), enter the URL and API key to search hundreds of additional indexers on top of the built-in NYAA and TPB sources.

### 4. (Optional) Set Up Notifications

Paste an [Apprise URL](https://github.com/caronc/apprise#supported-notifications) to receive a notification whenever a watchlist item is auto-downloaded. Examples:

| Service | URL format |
|---|---|
| Telegram | `tgram://BotToken/ChatID` |
| Discord | `discord://WebhookID/WebhookToken` |
| Slack | `slack://TokenA/TokenB/TokenC/Channel` |
| Email | `mailto://user:pass@gmail.com` |

---

## Usage

### Searching & Downloading

1. Go to **Search**
2. Type a show name, episode (e.g. `Breaking Bad S01E01`), or any keyword
3. Filter by quality (`1080p`, `4K`, etc.) and source if needed
4. Click **Download** on a result → pick a folder → done
5. Or click **Watchlist** to start auto-tracking the show

### Watchlist (Auto-download)

1. Go to **Watchlist** → **Add show**
2. Enter the show title, search query, quality preference, and which season/episode to start from
3. AutoRrent will search for new episodes on your configured interval (default: every 60 minutes) and download them automatically
4. Use **Scan now** on any item to trigger an immediate check
5. Toggle the switch on a card to pause/resume tracking

### Downloads

The Downloads page shows all torrents added through AutoRrent with live status from qBittorrent — including progress bars, download speed, and estimated time remaining. The page refreshes automatically every 10 seconds.

---

## NAS / Docker Volume Setup

If qBittorrent runs in Docker and your NAS is mounted on the host, make sure the path in AutoRrent's Settings matches the path **as qBittorrent sees it** (inside its container), not the host path.

To give the AutoRrent container visibility into your NAS (useful if you want to verify paths exist), uncomment the volume line in `docker-compose.yml`:

```yaml
volumes:
  - ./data:/app/data
  - /mnt/nas:/mnt/nas   # <-- uncomment this
```

---

## Configuration Reference

All settings are managed through the **Settings** page in the UI. There are no required environment variables.

| Setting | Default | Description |
|---|---|---|
| qBittorrent host | `localhost` | IP or hostname of your qBittorrent instance |
| qBittorrent port | `8080` | qBittorrent Web UI port |
| Category | `autorrent` | qBittorrent category assigned to all downloads |
| Scan interval | `60` minutes | How often the watchlist is checked for new episodes |
| Minimum seeds | `3` | Torrents with fewer seeds than this are skipped |
| Jackett URL | — | Optional: `http://your-server:9117` |
| Jackett API key | — | Found in Jackett's dashboard |
| Apprise URL | — | Optional notification endpoint |

---

## Supported Indexers

| Indexer | Built-in | Notes |
|---|---|---|
| NYAA.si | Yes | Best for anime |
| The Pirate Bay | Yes | General content |
| Jackett | Optional | Aggregates 100+ indexers |
| Prowlarr | Optional | Modern alternative to Jackett |

---

## Development

**Requirements:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mkdir -p /app/data   # or change DATABASE_URL
DATABASE_URL=sqlite:///./data/autorrent.db uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The frontend dev server runs on `http://localhost:5173` and proxies `/api` requests to `http://localhost:8000`.

---

## Tech Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy, SQLite, APScheduler, qbittorrentapi, httpx
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS, Lucide icons
- **Deployment:** Docker (multi-stage build), Docker Compose

---

## License

MIT
