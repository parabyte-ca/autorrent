from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, BigInteger, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .database import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    search_query = Column(String, nullable=False)
    quality = Column(String, default="1080p")
    season = Column(Integer, default=1)
    episode = Column(Integer, default=1)
    download_path_id = Column(Integer, ForeignKey("download_paths.id"), nullable=True)
    codec = Column(String, default="x265", server_default="x265")
    enabled = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    last_found = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    show_status = Column(String, nullable=True)
    show_status_checked_at = Column(DateTime, nullable=True)
    tvmaze_id = Column(Integer, nullable=True)
    show_status_override = Column(Boolean, default=False, server_default="0")

    # Download quality controls
    max_size_mb = Column(Integer, nullable=True)          # reject results larger than this
    preferred_groups = Column(String, nullable=True)      # comma-separated release groups, ranked
    prefer_season_packs = Column(Boolean, default=False, server_default="0")

    # Optional reusable quality profile; when set it overrides the per-item
    # quality / codec / size / groups / season-pack fields above.
    profile_id = Column(Integer, ForeignKey("quality_profiles.id"), nullable=True)

    episodes = relationship(
        "WatchlistEpisode",
        back_populates="watchlist_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class QualityProfile(Base):
    """Named, reusable download preferences assignable to watchlist items."""
    __tablename__ = "quality_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    quality = Column(String, nullable=False, default="1080p", server_default="1080p")
    codec = Column(String, nullable=False, default="x265", server_default="x265")
    max_size_mb = Column(Integer, nullable=True)
    preferred_groups = Column(String, nullable=True)
    prefer_season_packs = Column(Boolean, default=False, server_default="0")
    # If nothing is found at `quality` this long after an episode aired, try this.
    fallback_quality = Column(String, nullable=True)
    fallback_after_hours = Column(Integer, nullable=False, default=24, server_default="24")


class WatchlistEpisode(Base):
    """Persistent record of every episode successfully sent to qBittorrent via the watchlist.

    The UniqueConstraint on (watchlist_id, season, episode) is the authoritative
    deduplication guard — the scheduler catches IntegrityError and continues.
    ondelete="CASCADE" ensures rows are removed when the parent watchlist item
    is deleted, without requiring application-level cleanup.
    """
    __tablename__ = "watchlist_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watchlist_id = Column(
        Integer,
        ForeignKey("watchlist.id", ondelete="CASCADE"),
        nullable=False,
    )
    season = Column(Integer, nullable=False)
    episode = Column(Integer, nullable=False)
    downloaded_at = Column(DateTime, nullable=False, server_default=func.now())
    torrent_hash = Column(String, nullable=True)
    torrent_name = Column(String, nullable=True)

    watchlist_item = relationship("WatchlistItem", back_populates="episodes")

    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "season", "episode",
            name="uq_watchlist_season_episode",
        ),
    )


class Download(Base):
    __tablename__ = "downloads"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    torrent_hash = Column(String, nullable=True, index=True)
    magnet_link = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    status = Column(String, default="adding")
    download_path = Column(String, nullable=True)
    watchlist_id = Column(Integer, ForeignKey("watchlist.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    # Timestamp of first poll where qBittorrent reported a complete state.
    # Used to enforce the 60-second grace period before auto-removal.
    completion_first_seen_at = Column(DateTime, nullable=True)
    # True once the torrent has been successfully deleted from qBittorrent.
    qbit_removed = Column(Boolean, default=False, server_default="0")
    # Alert de-duplication: each event notifies at most once per download.
    notified_complete = Column(Boolean, default=False, server_default="0")
    notified_failed = Column(Boolean, default=False, server_default="0")
    notified_stalled = Column(Boolean, default=False, server_default="0")
    # Progress tracking used for real stall detection (no progress for N hours).
    last_progress = Column(Float, nullable=True)
    last_progress_at = Column(DateTime, nullable=True)
    # How many automatic re-searches led to this download (0 = original grab).
    retry_count = Column(Integer, default=0, server_default="0")


class DownloadPath(Base):
    __tablename__ = "download_paths"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    path = Column(String, nullable=False)
    is_default = Column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)


class DownloadHistory(Base):
    """Persistent record of every torrent sent to qBittorrent.

    Created immediately after a successful add_torrent() call (source may be
    "manual" from the Search page or "watchlist" from the auto-scanner).
    Status is updated in-place as qBittorrent reports progress.
    """
    __tablename__ = "download_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Torrent display name
    name = Column(String, nullable=False)
    # "manual" | "watchlist"
    source = Column(String, nullable=False)
    # Indexer the result came from, e.g. "nyaa", "tpb", "jackett/PublicHD"
    indexer = Column(String, nullable=True)
    # Destination folder display name (not the Linux path)
    folder = Column(String, nullable=True)
    # qBittorrent info-hash — used to match completion events
    torrent_hash = Column(String, nullable=True, index=True)
    # Total torrent size in bytes (may be populated later from qBittorrent)
    size_bytes = Column(BigInteger, nullable=True)
    # When the torrent was submitted to qBittorrent
    added_at = Column(DateTime, nullable=False, server_default=func.now())
    # Set when qBittorrent reports the torrent is seeding/completed
    completed_at = Column(DateTime, nullable=True)
    # "downloading" | "completed" | "failed"
    status = Column(String, nullable=False, default="downloading")
    # Populated when source == "watchlist"
    watchlist_id = Column(Integer, ForeignKey("watchlist.id"), nullable=True)
    # Populated when status == "failed"
    error_msg = Column(String, nullable=True)
