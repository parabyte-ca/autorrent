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
