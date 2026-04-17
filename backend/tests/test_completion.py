"""
Unit tests for completion status accuracy and qBittorrent auto-cleanup.

Coverage
────────
  State mapping  — each state in QBITTORRENT_COMPLETE_STATES triggers "completed"
  Status display — DOWNLOADING_STATES and ERROR_STATES map correctly
  Grace period   — deletion not called before 60 seconds
  Grace period   — deletion called after 60 seconds, qbit_removed set
  Deletion fail  — warning logged, qbit_removed stays False, retries next poll
  Absent torrent — already-absent case treated as successful removal
  Moving state   — completion_first_seen_at NOT set while "moving"
  History update — _update_history_completed called on first completion detection
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Download, DownloadHistory
from app.routers.downloads import QBITTORRENT_COMPLETE_STATES, router


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


def _make_download(db, **kwargs) -> Download:
    defaults = dict(
        title="Test Show S01E01",
        torrent_hash="abc123def456",
        status="downloading",
        created_at=datetime(2026, 1, 1),
        qbit_removed=False,
        completion_first_seen_at=None,
    )
    defaults.update(kwargs)
    d = Download(**defaults)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _make_history(db, torrent_hash="abc123def456", status="downloading"):
    h = DownloadHistory(
        name="Test Show S01E01",
        source="manual",
        torrent_hash=torrent_hash,
        status=status,
        added_at=datetime(2026, 1, 1),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


# ── QBITTORRENT_COMPLETE_STATES mapping ──────────────────────────────────────


class TestCompletionStateMapping:
    @pytest.mark.parametrize("qbit_state", sorted(QBITTORRENT_COMPLETE_STATES))
    def test_complete_state_sets_status_completed(self, qbit_state, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": qbit_state, "progress": 1.0, "size": 1000}
            resp = client.get("/downloads")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["status"] == "completed"

        db_session.refresh(d)
        assert d.status == "completed"

    def test_downloading_state_keeps_downloading_status(self, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            mock_gts.return_value = {"status": "downloading", "progress": 0.5}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.status == "downloading"

    def test_error_state_sets_error_status(self, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            mock_gts.return_value = {"status": "error", "progress": 0.0}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.status == "error"

    def test_stalledDL_not_treated_as_complete(self, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            mock_gts.return_value = {"status": "stalledDL", "progress": 0.3}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.status == "downloading"


# ── completion_first_seen_at and history ─────────────────────────────────────


class TestCompletionTracking:
    def test_completion_first_seen_at_set_on_first_detection(self, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.completion_first_seen_at is not None

    def test_completion_first_seen_at_not_overwritten_on_second_poll(self, client, db_session):
        first_seen = datetime(2026, 3, 1, 12, 0, 0)
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=first_seen,
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.completion_first_seen_at == first_seen

    def test_moving_state_does_not_set_completion_first_seen_at(self, client, db_session):
        d = _make_download(db_session, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": "moving", "progress": 1.0}
            client.get("/downloads")

        db_session.refresh(d)
        assert d.completion_first_seen_at is None

    def test_history_updated_once_on_completion(self, client, db_session):
        d = _make_download(db_session, status="downloading")
        h = _make_history(db_session, torrent_hash=d.torrent_hash, status="downloading")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0, "size": 5000}
            client.get("/downloads")

        db_session.refresh(h)
        assert h.status == "completed"
        assert h.completed_at is not None

    def test_history_not_updated_again_on_second_poll(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime(2026, 3, 1, 12, 0, 0),
        )
        h = _make_history(db_session, torrent_hash=d.torrent_hash, status="completed")

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            client.get("/downloads")

        db_session.refresh(h)
        # Status already "completed" — _update_history_completed only updates
        # rows with status == "downloading", so this stays "completed" and
        # completed_at should remain unchanged (None in this test).
        assert h.status == "completed"


# ── Grace period ──────────────────────────────────────────────────────────────


class TestGracePeriod:
    def test_deletion_not_called_before_grace_period(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=30),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent") as mock_remove:
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            client.get("/downloads")

        mock_remove.assert_not_called()
        db_session.refresh(d)
        assert not d.qbit_removed

    def test_deletion_called_after_grace_period(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=61),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent") as mock_remove, \
             patch("app.routers.downloads._fire_media_refresh"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            mock_remove.return_value = None
            client.get("/downloads")

        mock_remove.assert_called_once_with(d.torrent_hash, delete_files=False)
        db_session.refresh(d)
        assert d.qbit_removed

    def test_delete_files_is_always_false(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=61),
        )

        calls = []

        def _capture_remove(hash_, *, delete_files):
            calls.append(delete_files)

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent", side_effect=_capture_remove), \
             patch("app.routers.downloads._fire_media_refresh"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            client.get("/downloads")

        assert calls == [False], "delete_files must always be False"


# ── Deletion failure / retry ──────────────────────────────────────────────────


class TestDeletionFailure:
    def test_deletion_failure_does_not_set_qbit_removed(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=61),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent") as mock_remove, \
             patch("app.routers.downloads._fire_media_refresh"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            mock_remove.side_effect = Exception("qbittorrent unreachable")
            client.get("/downloads")

        db_session.refresh(d)
        assert not d.qbit_removed

    def test_deletion_retried_on_next_poll(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=61),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts, \
             patch("app.routers.downloads.remove_torrent") as mock_remove, \
             patch("app.routers.downloads._fire_media_refresh"):
            mock_gts.return_value = {"status": "uploading", "progress": 1.0}
            mock_remove.side_effect = [Exception("first failure"), None]

            client.get("/downloads")   # first poll — fails
            client.get("/downloads")   # second poll — succeeds

        assert mock_remove.call_count == 2
        db_session.refresh(d)
        assert d.qbit_removed


# ── Torrent absent from qBittorrent ──────────────────────────────────────────


class TestAlreadyAbsent:
    def test_absent_torrent_past_grace_period_marked_removed(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=61),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            # Returning None simulates torrent not found in qBittorrent
            mock_gts.return_value = None
            client.get("/downloads")

        db_session.refresh(d)
        assert d.qbit_removed

    def test_absent_torrent_before_grace_period_not_marked_removed(self, client, db_session):
        d = _make_download(
            db_session,
            status="completed",
            completion_first_seen_at=datetime.utcnow() - timedelta(seconds=30),
        )

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            mock_gts.return_value = None
            client.get("/downloads")

        db_session.refresh(d)
        assert not d.qbit_removed

    def test_qbit_removed_torrents_not_queried(self, client, db_session):
        _make_download(db_session, status="completed", qbit_removed=True)

        with patch("app.routers.downloads.get_torrent_status") as mock_gts:
            client.get("/downloads")

        mock_gts.assert_not_called()

    def test_response_shows_completed_for_removed_torrent(self, client, db_session):
        _make_download(db_session, status="completed", qbit_removed=True)

        with patch("app.routers.downloads.get_torrent_status"):
            resp = client.get("/downloads")

        assert resp.json()[0]["status"] == "completed"
