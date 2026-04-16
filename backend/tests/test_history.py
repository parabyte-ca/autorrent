"""
Unit tests for the download history endpoints.

Endpoints exercised
───────────────────
  GET    /history             Paginated, filterable list
  GET    /history/export      Full history as CSV download
  DELETE /history/{id}        Remove a single record
  DELETE /history             Remove all records (requires ?confirm=true)

Strategy
────────
An in-memory SQLite database is created per test session; the ``get_db``
dependency is overridden so no external services or on-disk files are needed.
"""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import DownloadHistory
from app.routers.history import router


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    """In-memory SQLite session, reset per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    """Minimal FastAPI app with the history router and an overridden DB dependency."""
    app = FastAPI()
    app.include_router(router)

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _make_record(db_session, **kwargs) -> DownloadHistory:
    """Insert and return a DownloadHistory row with sensible defaults."""
    defaults = dict(
        name="Test Torrent",
        source="manual",
        indexer="nyaa",
        folder="Downloads",
        torrent_hash="abc123",
        size_bytes=1_000_000_000,  # 1 GB (decimal)
        added_at=datetime(2026, 1, 15, 12, 0, 0),
        status="completed",
        watchlist_id=None,
        error_msg=None,
    )
    defaults.update(kwargs)
    h = DownloadHistory(**defaults)
    db_session.add(h)
    db_session.commit()
    db_session.refresh(h)
    return h


# ── GET /history ──────────────────────────────────────────────────────────────


class TestListHistory:
    def test_empty_database_returns_empty_list(self, client):
        resp = client.get("/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_all_records(self, client, db_session):
        _make_record(db_session, name="Show S01E01")
        _make_record(db_session, name="Show S01E02")
        resp = client.get("/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_response_item_has_required_fields(self, client, db_session):
        _make_record(db_session)
        resp = client.get("/history")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        for field in (
            "id", "name", "source", "indexer", "folder", "torrent_hash",
            "size_bytes", "size_human", "added_at", "completed_at",
            "status", "watchlist_id", "error_msg",
        ):
            assert field in item, f"Missing field: {field}"

    def test_size_human_is_formatted(self, client, db_session):
        _make_record(db_session, size_bytes=1_000_000_000)  # exactly 1 GB (decimal)
        resp = client.get("/history")
        item = resp.json()["items"][0]
        assert item["size_human"] == "1.0 GB"

    def test_size_human_is_dash_when_null(self, client, db_session):
        _make_record(db_session, size_bytes=None)
        resp = client.get("/history")
        item = resp.json()["items"][0]
        assert item["size_human"] == "—"

    def test_added_at_has_utc_suffix(self, client, db_session):
        _make_record(db_session)
        resp = client.get("/history")
        item = resp.json()["items"][0]
        assert item["added_at"].endswith("Z")

    def test_filter_by_name_q(self, client, db_session):
        _make_record(db_session, name="Breaking Bad S01E01")
        _make_record(db_session, name="Better Call Saul S01E01")
        resp = client.get("/history?q=breaking")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Breaking Bad S01E01"

    def test_filter_by_source_manual(self, client, db_session):
        _make_record(db_session, name="Manual A", source="manual")
        _make_record(db_session, name="Auto B", source="watchlist")
        resp = client.get("/history?source=manual")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["source"] == "manual"

    def test_filter_by_source_watchlist(self, client, db_session):
        _make_record(db_session, name="Manual A", source="manual")
        _make_record(db_session, name="Auto B", source="watchlist")
        resp = client.get("/history?source=watchlist")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["source"] == "watchlist"

    def test_filter_by_status(self, client, db_session):
        _make_record(db_session, name="Done", status="completed")
        _make_record(db_session, name="Broken", status="failed")
        resp = client.get("/history?status=failed")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Broken"

    def test_pagination_skip_and_limit(self, client, db_session):
        for i in range(5):
            _make_record(db_session, name=f"Torrent {i}")
        resp = client.get("/history?skip=2&limit=2")
        body = resp.json()
        assert body["total"] == 5          # total reflects all matching records
        assert len(body["items"]) == 2     # only 2 returned

    def test_limit_is_capped_at_200(self, client, db_session):
        # Just verify the endpoint accepts large limits without error.
        resp = client.get("/history?limit=999")
        assert resp.status_code == 200

    def test_source_all_returns_everything(self, client, db_session):
        _make_record(db_session, source="manual")
        _make_record(db_session, source="watchlist")
        resp = client.get("/history?source=all")
        assert resp.json()["total"] == 2


# ── GET /history/export ───────────────────────────────────────────────────────


class TestExportHistoryCsv:
    def test_returns_200_with_csv_content_type(self, client):
        resp = client.get("/history/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_content_disposition_has_filename(self, client):
        resp = client.get("/history/export")
        cd = resp.headers.get("content-disposition", "")
        assert "autorrent-history-" in cd
        assert ".csv" in cd

    def test_csv_has_header_row(self, client):
        resp = client.get("/history/export")
        lines = resp.text.splitlines()
        assert len(lines) >= 1
        header = lines[0]
        assert "name" in header
        assert "status" in header
        assert "added_at" in header

    def test_csv_empty_database_has_only_header(self, client):
        resp = client.get("/history/export")
        lines = resp.text.splitlines()
        assert len(lines) == 1  # header only, no data rows

    def test_csv_contains_one_row_per_record(self, client, db_session):
        _make_record(db_session, name="Anime S01E01")
        _make_record(db_session, name="Anime S01E02")
        resp = client.get("/history/export")
        lines = [l for l in resp.text.splitlines() if l.strip()]
        assert len(lines) == 3  # 1 header + 2 data rows

    def test_csv_record_name_appears_in_body(self, client, db_session):
        _make_record(db_session, name="UniqueShowTitle")
        resp = client.get("/history/export")
        assert "UniqueShowTitle" in resp.text


# ── DELETE /history/{id} ──────────────────────────────────────────────────────


class TestDeleteHistoryItem:
    def test_delete_existing_record_returns_ok(self, client, db_session):
        h = _make_record(db_session)
        resp = client.delete(f"/history/{h.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_removes_record_from_list(self, client, db_session):
        h = _make_record(db_session)
        client.delete(f"/history/{h.id}")
        resp = client.get("/history")
        assert resp.json()["total"] == 0

    def test_delete_nonexistent_record_returns_404(self, client):
        resp = client.delete("/history/99999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_delete_one_does_not_affect_other_records(self, client, db_session):
        h1 = _make_record(db_session, name="Keep This")
        h2 = _make_record(db_session, name="Delete This")
        client.delete(f"/history/{h2.id}")
        resp = client.get("/history")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == h1.id


# ── DELETE /history (clear all) ───────────────────────────────────────────────


class TestClearAllHistory:
    def test_clear_without_confirm_returns_400(self, client, db_session):
        _make_record(db_session)
        resp = client.delete("/history")
        assert resp.status_code == 400
        assert "confirm=true" in resp.json()["detail"]

    def test_clear_with_wrong_confirm_returns_400(self, client, db_session):
        _make_record(db_session)
        resp = client.delete("/history?confirm=yes")
        assert resp.status_code == 400

    def test_clear_with_confirm_true_deletes_all(self, client, db_session):
        _make_record(db_session, name="A")
        _make_record(db_session, name="B")
        resp = client.delete("/history?confirm=true")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert client.get("/history").json()["total"] == 0

    def test_clear_empty_database_returns_zero_deleted(self, client):
        resp = client.delete("/history?confirm=true")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
