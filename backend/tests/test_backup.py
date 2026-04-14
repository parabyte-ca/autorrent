"""
Unit tests for GET /backup/export and POST /backup/restore.

Strategy
────────
A minimal FastAPI app is constructed that includes only the backup router.
The ``get_db`` dependency is overridden with a real, on-disk SQLite database
(backed by a pytest ``tmp_path`` fixture) so that backup I/O is exercised
against actual files rather than mocks.

``_DB_PATH`` (the module-level constant that locates the live SQLite file) and
``get_scheduler`` (the APScheduler accessor) are patched via ``unittest.mock``
so the tests run without a running scheduler or a production database.
"""
import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.routers.backup import router


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path):
    """Real on-disk SQLite database; required because backup reads the raw file."""
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return str(db_file), sessionmaker(bind=engine)


@pytest.fixture()
def api_client(tmp_db):
    """Minimal FastAPI app with the backup router, a test DB, and a mocked scheduler."""
    db_path, Session = tmp_db

    app = FastAPI()
    app.include_router(router)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db

    # Scheduler mock: report STATE_RUNNING (1) so pause/resume logic is exercised.
    mock_scheduler = MagicMock()
    mock_scheduler.state = 1
    mock_scheduler.running = True

    with (
        patch("app.routers.backup._DB_PATH", db_path),
        patch("app.routers.backup.get_scheduler", return_value=mock_scheduler),
    ):
        yield TestClient(app), db_path, mock_scheduler


def _make_valid_zip(db_path: str) -> bytes:
    """Return bytes of a well-formed backup ZIP built from the test database."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        with open(db_path, "rb") as fh:
            zf.writestr("autorrent.db", fh.read())
        zf.writestr("settings.json", json.dumps({}))
        zf.writestr(
            "backup_meta.json",
            json.dumps(
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "app_version": "test",
                    "db_path": db_path,
                }
            ),
        )
    return buf.getvalue()


# ── Export tests ──────────────────────────────────────────────────────────────


class TestExportBackup:
    def test_returns_200_with_zip_content_type(self, api_client):
        client, _, _ = api_client
        resp = client.get("/backup/export")
        assert resp.status_code == 200
        assert "application/zip" in resp.headers["content-type"]

    def test_zip_contains_required_files(self, api_client):
        client, _, _ = api_client
        resp = client.get("/backup/export")
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()

        assert "autorrent.db" in names
        assert "settings.json" in names
        assert "backup_meta.json" in names

    def test_backup_meta_is_valid_json_with_required_keys(self, api_client):
        client, _, _ = api_client
        resp = client.get("/backup/export")
        assert resp.status_code == 200

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            meta = json.loads(zf.read("backup_meta.json"))

        assert "created_at" in meta
        assert "app_version" in meta
        assert "db_path" in meta

    def test_content_disposition_has_date_in_filename(self, api_client):
        client, _, _ = api_client
        resp = client.get("/backup/export")
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "autorrent-backup-" in cd
        assert ".zip" in cd


# ── Restore tests ─────────────────────────────────────────────────────────────


class TestRestoreBackup:
    def test_restore_valid_zip_returns_ok(self, api_client):
        client, db_path, _ = api_client
        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", _make_valid_zip(db_path), "application/zip")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "message" in body

    def test_restore_valid_zip_pauses_and_resumes_scheduler(self, api_client):
        client, db_path, mock_sched = api_client
        client.post(
            "/backup/restore",
            files={"file": ("backup.zip", _make_valid_zip(db_path), "application/zip")},
        )
        mock_sched.pause.assert_called_once()
        mock_sched.resume.assert_called_once()

    def test_restore_valid_zip_overwrites_database_file(self, api_client):
        """After restore the DB file should contain the bytes from the backup."""
        client, db_path, _ = api_client
        zip_bytes = _make_valid_zip(db_path)

        # Record original DB size; backup contains the same file so sizes match.
        original_size = len(open(db_path, "rb").read())

        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", zip_bytes, "application/zip")},
        )
        assert resp.status_code == 200
        restored_size = len(open(db_path, "rb").read())
        assert restored_size == original_size

    def test_restore_missing_autorrent_db_returns_400(self, api_client):
        client, _, _ = api_client
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "backup_meta.json",
                json.dumps({"created_at": "", "app_version": "test", "db_path": ""}),
            )
            # autorrent.db intentionally absent
        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 400
        assert "autorrent.db" in resp.json()["detail"]

    def test_restore_missing_backup_meta_returns_400(self, api_client):
        client, db_path, _ = api_client
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            with open(db_path, "rb") as fh:
                zf.writestr("autorrent.db", fh.read())
            # backup_meta.json intentionally absent
        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 400
        assert "backup_meta.json" in resp.json()["detail"]

    def test_restore_corrupt_zip_returns_400(self, api_client):
        client, _, _ = api_client
        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", b"this is definitely not a zip", "application/zip")},
        )
        assert resp.status_code == 400
        assert "Invalid ZIP" in resp.json()["detail"]

    def test_restore_corrupt_meta_json_returns_400(self, api_client):
        client, db_path, _ = api_client
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            with open(db_path, "rb") as fh:
                zf.writestr("autorrent.db", fh.read())
            zf.writestr("backup_meta.json", "not valid json {{{{")
        resp = client.post(
            "/backup/restore",
            files={"file": ("backup.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 400
        assert "backup_meta.json" in resp.json()["detail"]
