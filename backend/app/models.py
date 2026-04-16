from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, BigInteger
from sqlalchemy.sql import func
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
