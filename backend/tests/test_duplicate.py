"""
Unit tests for duplicate detection.

Coverage
────────
  normalize_name — punctuation, casing, spaces
  check_duplicate — hash match, name match, active qBittorrent match, no match
  POST /downloads — 409 on duplicate with force=False
  POST /downloads — 201 on duplicate with force=True
  POST /downloads/check-duplicate — HTTP 200 always
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import DownloadHistory
from app.routers.downloads import router
from app.services.duplicate import check_duplicate, normalize_name


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_pragma(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(router)

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _make_history(db, name="Show S01E01 1080p", torrent_hash="abc123", **kwargs):
    row = DownloadHistory(
        name=name,
        source=kwargs.get("source", "manual"),
        torrent_hash=torrent_hash,
        added_at=kwargs.get("added_at", datetime(2026, 3, 1, 12, 0, 0)),
        status="completed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── normalize_name ────────────────────────────────────────────────────────────


class TestNormalizeName:
    def test_strips_dots_and_lowercases(self):
        assert normalize_name("Show.Name.S01E04.1080p.BluRay") == "show name s01e04 1080p bluray"

    def test_strips_hyphens(self):
        assert normalize_name("Show-Name-S01E04") == "show name s01e04"

    def test_collapses_multiple_spaces(self):
        assert normalize_name("show  name   s01e04") == "show name s01e04"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_name("  show name  ") == "show name"

    def test_strips_brackets_and_parens(self):
        assert normalize_name("[HorribleSubs] Show (1080p)") == "horriblestps show 1080p".replace(
            "horriblestps", "horriblesubs"
        )

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_all_special_chars(self):
        assert normalize_name("...---...") == ""

    def test_mixed_case(self):
        assert normalize_name("Breaking BAD S01E01") == "breaking bad s01e01"

    def test_numbers_preserved(self):
        assert normalize_name("Show 2024 S02E03") == "show 2024 s02e03"


# ── check_duplicate ───────────────────────────────────────────────────────────


class TestCheckDuplicate:
    def test_hash_match_returns_duplicate(self, db_session):
        _make_history(db_session, name="Show S01E01", torrent_hash="aabbcc1122")

        result = check_duplicate("aabbcc1122", "Any Name", db_session)

        assert result["is_duplicate"] is True
        assert result["match_type"] == "hash"
        assert result["matched_name"] == "Show S01E01"
        assert result["matched_at"] is not None

    def test_hash_match_is_case_insensitive(self, db_session):
        _make_history(db_session, torrent_hash="aabbcc1122")

        result = check_duplicate("AABBCC1122", "Any Name", db_session)

        assert result["is_duplicate"] is True
        assert result["match_type"] == "hash"

    def test_name_match_returns_duplicate(self, db_session):
        _make_history(db_session, name="Show.Name.S01E01.1080p", torrent_hash="different")

        result = check_duplicate("deadbeef", "Show Name S01E01 1080p", db_session)

        assert result["is_duplicate"] is True
        assert result["match_type"] == "name"
        assert result["matched_name"] == "Show.Name.S01E01.1080p"

    def test_name_match_ignores_punctuation(self, db_session):
        _make_history(db_session, name="[Group] Show - S01E01 (1080p)", torrent_hash="x1")

        result = check_duplicate(None, "Group Show S01E01 1080p", db_session)

        assert result["is_duplicate"] is True
        assert result["match_type"] == "name"

    def test_active_qbit_match_returns_duplicate(self, db_session):
        mock_torrent = MagicMock()
        mock_torrent.name = "Active Show S01E01"
        mock_client = MagicMock()
        mock_client.torrents_info.return_value = [mock_torrent]

        with patch("app.services.qbittorrent._get_client", return_value=mock_client):
            result = check_duplicate("activehash123", "No Match In DB", db_session)

        assert result["is_duplicate"] is True
        assert result["match_type"] == "active"
        assert result["matched_name"] == "Active Show S01E01"
        assert result["matched_at"] is None

    def test_no_match_returns_not_duplicate(self, db_session):
        mock_client = MagicMock()
        mock_client.torrents_info.return_value = []

        with patch("app.services.qbittorrent._get_client", return_value=mock_client):
            result = check_duplicate("nomatch", "Brand New Show S01E01", db_session)

        assert result["is_duplicate"] is False
        assert result["match_type"] is None
        assert result["matched_name"] is None

    def test_hash_match_takes_priority_over_name_match(self, db_session):
        _make_history(db_session, name="Show Hash Match", torrent_hash="hashmatch")
        _make_history(db_session, name="Show Name Match", torrent_hash="other")

        result = check_duplicate("hashmatch", "Show Name Match", db_session)

        assert result["match_type"] == "hash"
        assert result["matched_name"] == "Show Hash Match"

    def test_qbit_unreachable_does_not_raise(self, db_session):
        with patch("app.services.qbittorrent._get_client", side_effect=Exception("connection refused")):
            result = check_duplicate("somehash", "Some Show", db_session)

        assert result["is_duplicate"] is False

    def test_none_hash_skips_hash_and_active_checks(self, db_session):
        _make_history(db_session, name="Exact Match Show", torrent_hash="abc")

        with patch("app.services.qbittorrent._get_client") as mock_get_client:
            result = check_duplicate(None, "Exact Match Show", db_session)
            mock_get_client.assert_not_called()

        assert result["is_duplicate"] is True
        assert result["match_type"] == "name"

    def test_matched_at_has_utc_z_suffix(self, db_session):
        _make_history(db_session, torrent_hash="zzz")

        result = check_duplicate("zzz", "Anything", db_session)

        assert result["matched_at"] is not None
        assert result["matched_at"].endswith("Z")


# ── Download endpoint — duplicate handling ────────────────────────────────────

# Magnet containing hash "aabbccddeeff1122334455667788990011223344"
_MAGNET = "magnet:?xt=urn:btih:aabbccddeeff1122334455667788990011223344&dn=Test+Show"
_HASH = "aabbccddeeff1122334455667788990011223344"


class TestDownloadEndpointDuplicate:
    def test_force_false_returns_409_on_hash_duplicate(self, client, db_session):
        _make_history(db_session, name="Test Show", torrent_hash=_HASH)

        with patch("app.services.qbittorrent._get_client", side_effect=Exception("skip qbit")):
            resp = client.post("/downloads", json={"magnet": _MAGNET, "title": "Test Show", "force": False})

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["duplicate"] is True
        assert body["detail"]["match_type"] == "hash"

    def test_force_true_proceeds_past_duplicate(self, client, db_session):
        _make_history(db_session, name="Test Show", torrent_hash=_HASH)

        mock_add = MagicMock(return_value=_HASH)
        with patch("app.routers.downloads.add_torrent", mock_add), \
             patch("app.services.qbittorrent._get_client", side_effect=Exception("skip qbit")):
            resp = client.post("/downloads", json={"magnet": _MAGNET, "title": "Test Show", "force": True})

        assert resp.status_code == 201
        mock_add.assert_called_once()

    def test_no_duplicate_proceeds_normally(self, client, db_session):
        mock_add = MagicMock(return_value=_HASH)
        mock_client = MagicMock()
        mock_client.torrents_info.return_value = []

        with patch("app.routers.downloads.add_torrent", mock_add), \
             patch("app.services.qbittorrent._get_client", return_value=mock_client):
            resp = client.post("/downloads", json={"magnet": _MAGNET, "title": "Brand New Show"})

        assert resp.status_code == 201
        mock_add.assert_called_once()


# ── POST /downloads/check-duplicate ──────────────────────────────────────────


class TestCheckDuplicateEndpoint:
    def test_returns_200_on_duplicate(self, client, db_session):
        _make_history(db_session, name="Known Show", torrent_hash="knownhash")

        resp = client.post("/downloads/check-duplicate",
                           json={"torrent_hash": "knownhash", "torrent_name": "Known Show"})

        assert resp.status_code == 200
        assert resp.json()["is_duplicate"] is True

    def test_returns_200_on_no_duplicate(self, client, db_session):
        mock_client = MagicMock()
        mock_client.torrents_info.return_value = []

        with patch("app.services.qbittorrent._get_client", return_value=mock_client):
            resp = client.post("/downloads/check-duplicate",
                               json={"torrent_hash": "newhash", "torrent_name": "Unknown Show"})

        assert resp.status_code == 200
        assert resp.json()["is_duplicate"] is False

    def test_accepts_null_hash(self, client, db_session):
        resp = client.post("/downloads/check-duplicate",
                           json={"torrent_hash": None, "torrent_name": "Unknown Show"})

        assert resp.status_code == 200
