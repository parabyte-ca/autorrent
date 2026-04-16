from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TorrentResult(BaseModel):
    title: str
    size: str
    size_bytes: int
    seeds: int
    leeches: int
    magnet: str
    info_hash: Optional[str] = None
    quality: Optional[str] = None
    source: str
    url: Optional[str] = None


# ── Download Paths ────────────────────────────────────────────────────────────

class DownloadPathCreate(BaseModel):
    name: str
    path: str
    is_default: bool = False


class DownloadPathUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    is_default: Optional[bool] = None


class DownloadPathOut(BaseModel):
    id: int
    name: str
    path: str
    is_default: bool

    model_config = {"from_attributes": True}


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    title: str
    search_query: str
    quality: str = "1080p"
    codec: str = "x265"
    season: int = 1
    episode: int = 1
    download_path_id: Optional[int] = None
    enabled: bool = True


class WatchlistUpdate(BaseModel):
    title: Optional[str] = None
    search_query: Optional[str] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    download_path_id: Optional[int] = None
    enabled: Optional[bool] = None


class WatchlistOut(BaseModel):
    id: int
    title: str
    search_query: str
    quality: str
    codec: str = "x265"
    season: int
    episode: int
    download_path_id: Optional[int] = None
    enabled: bool
    last_checked: Optional[datetime] = None
    last_found: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Downloads ─────────────────────────────────────────────────────────────────

class DownloadCreate(BaseModel):
    magnet: str
    title: str
    download_path_id: Optional[int] = None
    watchlist_id: Optional[int] = None
    # Indexer the torrent came from (e.g. "nyaa", "tpb") — passed from the
    # Search page so it can be recorded in the download history.
    indexer: Optional[str] = None


# ── Media server test connections ─────────────────────────────────────────────

class PlexTestRequest(BaseModel):
    url: str
    token: str
    library_key: Optional[str] = None


class JellyfinTestRequest(BaseModel):
    url: str
    api_key: str
