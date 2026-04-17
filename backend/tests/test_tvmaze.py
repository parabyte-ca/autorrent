"""
Unit tests for the TVMaze client and the show-status auto-pause logic.

Coverage
────────
  get_show_status — Running show (uses cached tvmaze_id)
  get_show_status — Ended show (search path)
  get_show_status — HTTP 404 from search → ("Unknown", None)
  get_show_status — network error → ("Unknown", None)
  get_show_status — unknown status string → "Unknown"
  _check_statuses_async — Running show leaves item enabled
  _check_statuses_async — Ended show auto-pauses item
  _check_statuses_async — override flag prevents auto-pause
  _check_statuses_async — tvmaze_id is persisted on first search
  _check_statuses_async — already-disabled items are skipped
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import WatchlistItem
from app.services.tvmaze import get_show_status


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


def _make_item(db, **kwargs) -> WatchlistItem:
    defaults = dict(
        title="Test Show",
        search_query="Test Show",
        quality="1080p",
        codec="x265",
        season=1,
        episode=3,
        enabled=True,
        created_at=datetime(2026, 1, 1),
    )
    defaults.update(kwargs)
    item = WatchlistItem(**defaults)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ── get_show_status unit tests ────────────────────────────────────────────────


class TestGetShowStatus:
    @pytest.mark.asyncio
    async def test_running_show_uses_cached_id(self):
        with patch("app.services.tvmaze.get_show", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"id": 42, "status": "Running"}
            status, tvmaze_id = await get_show_status("Some Show", tvmaze_id=42)

        assert status == "Running"
        assert tvmaze_id == 42
        mock_get.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_ended_show_via_search(self):
        with patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"id": 7, "status": "Ended"}
            status, tvmaze_id = await get_show_status("Old Show")

        assert status == "Ended"
        assert tvmaze_id == 7

    @pytest.mark.asyncio
    async def test_404_from_search_returns_unknown(self):
        with patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = None
            status, tvmaze_id = await get_show_status("Nonexistent Show")

        assert status == "Unknown"
        assert tvmaze_id is None

    @pytest.mark.asyncio
    async def test_network_error_returns_unknown(self):
        # get_show handles exceptions internally and returns None; simulate that.
        with patch("app.services.tvmaze.get_show", new_callable=AsyncMock) as mock_get, \
             patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_get.return_value = None
            mock_search.return_value = None
            status, tvmaze_id = await get_show_status("Any Show", tvmaze_id=99)

        assert status == "Unknown"
        assert tvmaze_id is None

    @pytest.mark.asyncio
    async def test_unknown_status_string_maps_to_unknown(self):
        with patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"id": 5, "status": "Some Future Status"}
            status, tvmaze_id = await get_show_status("Future Show")

        assert status == "Unknown"
        assert tvmaze_id == 5

    @pytest.mark.asyncio
    async def test_tbd_status_mapped_correctly(self):
        with patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"id": 11, "status": "To Be Determined"}
            status, _ = await get_show_status("TBD Show")

        assert status == "To Be Determined"

    @pytest.mark.asyncio
    async def test_falls_back_to_search_when_get_returns_none(self):
        with patch("app.services.tvmaze.get_show", new_callable=AsyncMock) as mock_get, \
             patch("app.services.tvmaze.search_show", new_callable=AsyncMock) as mock_search:
            mock_get.return_value = None
            mock_search.return_value = {"id": 13, "status": "Running"}
            status, tvmaze_id = await get_show_status("Some Show", tvmaze_id=13)

        assert status == "Running"
        assert tvmaze_id == 13


# ── Auto-pause logic (_check_statuses_async) ──────────────────────────────────


class TestCheckStatusesAsync:
    """Integration-style tests for _check_statuses_async using in-memory SQLite."""

    @pytest.mark.asyncio
    async def test_running_show_remains_enabled(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Running Show", enabled=True)

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Running", 10)
            await _check_statuses_async(db_session)


        db_session.refresh(item)
        assert item.enabled is True
        assert item.show_status == "Running"
        assert item.tvmaze_id == 10

    @pytest.mark.asyncio
    async def test_ended_show_auto_pauses(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Ended Show", enabled=True)

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss, \
             patch("app.services.apprise_notify.notify") as _:
            mock_gss.return_value = ("Ended", 20)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        assert item.enabled is False
        assert item.show_status == "Ended"

    @pytest.mark.asyncio
    async def test_override_prevents_auto_pause(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Override Show", enabled=True, show_status_override=True)

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Ended", 30)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        assert item.enabled is True

    @pytest.mark.asyncio
    async def test_tvmaze_id_persisted_on_first_search(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="New Show", enabled=True)
        assert item.tvmaze_id is None

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Running", 99)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        assert item.tvmaze_id == 99

    @pytest.mark.asyncio
    async def test_disabled_items_are_skipped(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Disabled Show", enabled=False)

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Ended", 50)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        # show_status unchanged (None) because item was skipped
        assert item.show_status is None

    @pytest.mark.asyncio
    async def test_item_id_filter_only_checks_one(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item_a = _make_item(db_session, title="Show A", enabled=True)
        item_b = _make_item(db_session, title="Show B", enabled=True)

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Ended", 60)
            await _check_statuses_async(db_session, item_id=item_a.id)

        db_session.refresh(item_a)
        db_session.refresh(item_b)
        assert item_a.show_status == "Ended"
        assert item_b.show_status is None

    @pytest.mark.asyncio
    async def test_show_status_checked_at_is_updated(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Some Show", enabled=True)
        assert item.show_status_checked_at is None

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Running", 77)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        assert item.show_status_checked_at is not None

    @pytest.mark.asyncio
    async def test_unknown_status_does_not_overwrite_previous(self, db_session):
        from app.services.scheduler import _check_statuses_async

        item = _make_item(db_session, title="Flaky Show", enabled=True)
        item.show_status = "Running"
        db_session.commit()

        with patch("app.services.tvmaze.get_show_status", new_callable=AsyncMock) as mock_gss:
            mock_gss.return_value = ("Unknown", None)
            await _check_statuses_async(db_session)

        db_session.refresh(item)
        assert item.show_status == "Running"
