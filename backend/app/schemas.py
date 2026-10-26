from pydantic import BaseModel, Field, field_serializer
from typing import Literal, Optional
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
    free_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    low_space: bool = False

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
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    profile_id: Optional[int] = None


class WatchlistUpdate(BaseModel):
    title: Optional[str] = None
    search_query: Optional[str] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    download_path_id: Optional[int] = None
    enabled: Optional[bool] = None
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: Optional[bool] = None
    profile_id: Optional[int] = None


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
    show_status: Optional[str] = None
    show_status_checked_at: Optional[datetime] = None
    tvmaze_id: Optional[int] = None
    show_status_override: bool = False
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    profile_id: Optional[int] = None
    poster_url: Optional[str] = None
    summary: Optional[str] = None
    network: Optional[str] = None
    premiered: Optional[str] = None
    metadata_checked_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Watchlist episode tracking ────────────────────────────────────────────────

class WatchlistEpisodeOut(BaseModel):
    id: int
    watchlist_id: int
    season: int
    episode: int
    downloaded_at: datetime
    torrent_hash: Optional[str] = None
    torrent_name: Optional[str] = None

    @field_serializer("downloaded_at")
    def serialize_downloaded_at(self, v: datetime) -> str:
        return v.isoformat() + "Z"

    model_config = {"from_attributes": True}


# ── Quality profiles ──────────────────────────────────────────────────────────

QualityName = Literal["4K", "1080p", "720p", "480p", "Any"]
CodecName = Literal["x265", "x264", "AV1", "Any"]


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    quality: QualityName = "1080p"
    codec: CodecName = "x265"
    max_size_mb: Optional[int] = Field(default=None, ge=1)
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    fallback_quality: Optional[QualityName] = None
    fallback_after_hours: int = Field(default=24, ge=1, le=24 * 30)


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    quality: Optional[QualityName] = None
    codec: Optional[CodecName] = None
    max_size_mb: Optional[int] = Field(default=None, ge=1)
    preferred_groups: Optional[str] = None
    prefer_season_packs: Optional[bool] = None
    fallback_quality: Optional[QualityName] = None
    fallback_after_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30)


class ProfileOut(BaseModel):
    id: int
    name: str
    quality: str
    codec: str
    max_size_mb: Optional[int] = None
    preferred_groups: Optional[str] = None
    prefer_season_packs: bool = False
    fallback_quality: Optional[str] = None
    fallback_after_hours: int = 24
    watchlist_count: int = 0

    model_config = {"from_attributes": True}


class AddFromTvmazeRequest(BaseModel):
    tvmaze_id: int
    start: Optional[Literal["next", "beginning"]] = None
    profile_id: Optional[int] = None


class TraktTestRequest(BaseModel):
    client_id: str
    username: str


class TmdbTestRequest(BaseModel):
    api_key: str


class JackettTestRequest(BaseModel):
    url: str
    api_key: str


class MarkEpisodeRequest(BaseModel):
    season: int
    episode: int
    torrent_name: Optional[str] = None


# ── Downloads ─────────────────────────────────────────────────────────────────

class DownloadCreate(BaseModel):
    magnet: str
    title: str
    download_path_id: Optional[int] = None
    watchlist_id: Optional[int] = None
    indexer: Optional[str] = None
    force: bool = False


class DuplicateCheckRequest(BaseModel):
    torrent_hash: Optional[str] = None
    torrent_name: str


# ── Media server test connections ─────────────────────────────────────────────

class PlexTestRequest(BaseModel):
    url: str
    token: str
    library_key: Optional[str] = None


class JellyfinTestRequest(BaseModel):
    url: str
    api_key: str


class DigestTestRequest(BaseModel):
    digest_smtp_host: str = ""
    digest_smtp_port: str = "587"
    digest_smtp_user: str = ""
    digest_smtp_password: str = ""
    digest_from_email: str = ""
    digest_recipients: str = ""
    plex_url: str = ""
    plex_token: str = ""
    digest_excluded_libs: str = ""
