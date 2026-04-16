"""
Unit tests for the WatchlistEpisode feature.

Coverage
────────
  Skip logic                — episode already in WatchlistEpisode table → guard fires
  Record creation           — WatchlistEpisode row is created and queryable
  Duplicate insert handling — IntegrityError on duplicate → graceful rollback
  Cascade delete            — deleting WatchlistItem removes all its episode rows
  GET    /watchlist/{id}/episodes            — list, ordering, 404
  POST   /watchlist/{id}/episodes            — mark, 409 on duplicate, 201 on success
  DELETE /watchlist/{id}/episodes/{ep_id}   — single delete, 404 for unknown
  DELETE /watchlist/{id}/episodes           — reset all (confirm=true), 400 without
"""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import WatchlistEpisode, WatchlistItem
from app.routers.watchlist import router


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite does not enforce foreign-key constraints by default.
    # Enabling the pragma ensures ondelete="CASCADE" actually fires.
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


def _make_item(db_session, **kwargs) -> WatchlistItem:
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
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _make_ep(db_session, watchlist_id: int, season: int, episode: int, **kw) -> WatchlistEpisode:
    ep = WatchlistEpisode(
        watchlist_id=watchlist_id,
        season=season,
        episode=episode,
        downloaded_at=kw.get("downloaded_at", datetime(2026, 4, 1, 10, 0, 0)),
        torrent_name=kw.get("torrent_name", f"Show.S{season:02d}E{episode:02d}.1080p"),
        torrent_hash=kw.get("torrent_hash", "abc123"),
    )
    db_session.add(ep)
    db_session.commit()
    db_session.refresh(ep)
    return ep


# ── Skip-logic / record-creation tests ───────────────────────────────────────


class TestSchedulerEpisodeGuard:
    """Verify the WatchlistEpisode table correctly serves as the skip guard."""

    def test_existing_record_makes_guard_fire(self, db_session):
        """When a WatchlistEpisode exists, the guard query returns a truthy result."""
        item = _make_item(db_session, season=1, episode=3)
        _make_ep(db_session, item.id, season=1, episode=3)

        already = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item.id, season=1, episode=3)
            .first()
        )
        assert already is not None, "Guard should find the existing record"

    def test_no_record_means_guard_passes(self, db_session):
        """When no WatchlistEpisode exists for an S/E, the guard query returns None."""
        item = _make_item(db_session, season=1, episode=3)

        already = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item.id, season=1, episode=3)
            .first()
        )
        assert already is None, "No record → guard should pass (allow download)"

    def test_guard_is_specific_to_season_and_episode(self, db_session):
        """A record for S01E02 does not block S01E03."""
        item = _make_item(db_session, season=1, episode=3)
        _make_ep(db_session, item.id, season=1, episode=2)  # different episode

        already = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item.id, season=1, episode=3)
            .first()
        )
        assert already is None

    def test_guard_is_specific_to_watchlist_item(self, db_session):
        """A record for watchlist item A does not block item B for the same S/E."""
        item_a = _make_item(db_session, title="Show A")
        item_b = _make_item(db_session, title="Show B")
        _make_ep(db_session, item_a.id, season=1, episode=3)

        already = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item_b.id, season=1, episode=3)
            .first()
        )
        assert already is None


class TestEpisodeRecordCreation:
    """Verify WatchlistEpisode rows can be created and are queryable."""

    def test_episode_record_is_stored_and_queryable(self, db_session):
        item = _make_item(db_session, season=2, episode=5)
        ep = WatchlistEpisode(
            watchlist_id=item.id,
            season=2,
            episode=5,
            downloaded_at=datetime.utcnow(),
            torrent_hash="deadbeef",
            torrent_name="Show S02E05 1080p",
        )
        db_session.add(ep)
        db_session.commit()

        fetched = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item.id, season=2, episode=5)
            .first()
        )
        assert fetched is not None
        assert fetched.torrent_hash == "deadbeef"
        assert fetched.torrent_name == "Show S02E05 1080p"

    def test_duplicate_insert_raises_integrity_error(self, db_session):
        """The UniqueConstraint prevents inserting the same S/E twice for a show."""
        from sqlalchemy.exc import IntegrityError

        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)

        db_session.add(WatchlistEpisode(watchlist_id=item.id, season=1, episode=1,
                                        downloaded_at=datetime.utcnow()))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_same_episode_on_different_shows_is_allowed(self, db_session):
        """S01E01 for show A and S01E01 for show B are independent rows."""
        item_a = _make_item(db_session, title="Show A")
        item_b = _make_item(db_session, title="Show B")
        _make_ep(db_session, item_a.id, season=1, episode=1)
        _make_ep(db_session, item_b.id, season=1, episode=1)  # should not raise

        count = db_session.query(WatchlistEpisode).filter_by(season=1, episode=1).count()
        assert count == 2

    def test_cascade_delete_removes_episodes(self, db_session):
        """Deleting a WatchlistItem cascades to its WatchlistEpisode rows."""
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)
        _make_ep(db_session, item.id, season=1, episode=2)

        db_session.delete(item)
        db_session.commit()

        remaining = (
            db_session.query(WatchlistEpisode)
            .filter_by(watchlist_id=item.id)
            .all()
        )
        assert remaining == []


# ── GET /watchlist/{id}/episodes ──────────────────────────────────────────────


class TestGetEpisodes:
    def test_returns_empty_list_when_no_episodes(self, client, db_session):
        item = _make_item(db_session)
        resp = client.get(f"/watchlist/{item.id}/episodes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_episodes_ordered_season_then_episode(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=2, episode=1)
        _make_ep(db_session, item.id, season=1, episode=3)
        _make_ep(db_session, item.id, season=1, episode=1)

        resp = client.get(f"/watchlist/{item.id}/episodes")
        data = resp.json()
        assert len(data) == 3
        assert (data[0]["season"], data[0]["episode"]) == (1, 1)
        assert (data[1]["season"], data[1]["episode"]) == (1, 3)
        assert (data[2]["season"], data[2]["episode"]) == (2, 1)

    def test_response_item_has_required_fields(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)
        ep = client.get(f"/watchlist/{item.id}/episodes").json()[0]
        for field in ("id", "watchlist_id", "season", "episode", "downloaded_at",
                      "torrent_hash", "torrent_name"):
            assert field in ep, f"Missing field: {field}"

    def test_downloaded_at_has_utc_z_suffix(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)
        ep = client.get(f"/watchlist/{item.id}/episodes").json()[0]
        assert ep["downloaded_at"].endswith("Z")

    def test_returns_404_for_unknown_watchlist_item(self, client):
        resp = client.get("/watchlist/99999/episodes")
        assert resp.status_code == 404

    def test_episodes_isolated_per_watchlist_item(self, client, db_session):
        item_a = _make_item(db_session, title="Show A")
        item_b = _make_item(db_session, title="Show B")
        _make_ep(db_session, item_a.id, season=1, episode=1)
        _make_ep(db_session, item_b.id, season=1, episode=1)

        data = client.get(f"/watchlist/{item_a.id}/episodes").json()
        assert len(data) == 1
        assert data[0]["watchlist_id"] == item_a.id


# ── POST /watchlist/{id}/episodes ─────────────────────────────────────────────


class TestMarkEpisode:
    def test_mark_returns_201_and_record(self, client, db_session):
        item = _make_item(db_session)
        resp = client.post(f"/watchlist/{item.id}/episodes",
                           json={"season": 1, "episode": 4})
        assert resp.status_code == 201
        data = resp.json()
        assert data["season"] == 1
        assert data["episode"] == 4
        assert data["watchlist_id"] == item.id

    def test_mark_stores_torrent_name(self, client, db_session):
        item = _make_item(db_session)
        resp = client.post(f"/watchlist/{item.id}/episodes",
                           json={"season": 1, "episode": 5, "torrent_name": "Show.S01E05.HEVC"})
        assert resp.status_code == 201
        assert resp.json()["torrent_name"] == "Show.S01E05.HEVC"

    def test_duplicate_returns_409(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=2)
        resp = client.post(f"/watchlist/{item.id}/episodes",
                           json={"season": 1, "episode": 2})
        assert resp.status_code == 409
        assert "already marked" in resp.json()["detail"].lower()

    def test_returns_404_for_unknown_watchlist(self, client):
        resp = client.post("/watchlist/99999/episodes", json={"season": 1, "episode": 1})
        assert resp.status_code == 404

    def test_marked_episode_visible_in_list(self, client, db_session):
        item = _make_item(db_session)
        client.post(f"/watchlist/{item.id}/episodes", json={"season": 3, "episode": 7})
        data = client.get(f"/watchlist/{item.id}/episodes").json()
        assert any(e["season"] == 3 and e["episode"] == 7 for e in data)


# ── DELETE /watchlist/{id}/episodes/{ep_id} ───────────────────────────────────


class TestDeleteEpisode:
    def test_delete_returns_204(self, client, db_session):
        item = _make_item(db_session)
        ep = _make_ep(db_session, item.id, season=1, episode=1)
        assert client.delete(f"/watchlist/{item.id}/episodes/{ep.id}").status_code == 204

    def test_delete_removes_record(self, client, db_session):
        item = _make_item(db_session)
        ep = _make_ep(db_session, item.id, season=1, episode=1)
        client.delete(f"/watchlist/{item.id}/episodes/{ep.id}")
        assert client.get(f"/watchlist/{item.id}/episodes").json() == []

    def test_delete_nonexistent_returns_404(self, client, db_session):
        item = _make_item(db_session)
        assert client.delete(f"/watchlist/{item.id}/episodes/99999").status_code == 404

    def test_delete_wrong_watchlist_returns_404(self, client, db_session):
        item_a = _make_item(db_session, title="Show A")
        item_b = _make_item(db_session, title="Show B")
        ep = _make_ep(db_session, item_a.id, season=1, episode=1)
        assert client.delete(f"/watchlist/{item_b.id}/episodes/{ep.id}").status_code == 404

    def test_delete_leaves_other_episodes_intact(self, client, db_session):
        item = _make_item(db_session)
        ep1 = _make_ep(db_session, item.id, season=1, episode=1)
        _make_ep(db_session, item.id, season=1, episode=2)
        client.delete(f"/watchlist/{item.id}/episodes/{ep1.id}")
        data = client.get(f"/watchlist/{item.id}/episodes").json()
        assert len(data) == 1
        assert data[0]["episode"] == 2


# ── DELETE /watchlist/{id}/episodes (reset all) ───────────────────────────────


class TestResetEpisodes:
    def test_reset_without_confirm_returns_400(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)
        resp = client.delete(f"/watchlist/{item.id}/episodes")
        assert resp.status_code == 400
        assert "confirm=true" in resp.json()["detail"]

    def test_reset_wrong_confirm_returns_400(self, client, db_session):
        item = _make_item(db_session)
        assert client.delete(f"/watchlist/{item.id}/episodes?confirm=yes").status_code == 400

    def test_reset_deletes_all_and_returns_count(self, client, db_session):
        item = _make_item(db_session)
        _make_ep(db_session, item.id, season=1, episode=1)
        _make_ep(db_session, item.id, season=1, episode=2)
        resp = client.delete(f"/watchlist/{item.id}/episodes?confirm=true")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert client.get(f"/watchlist/{item.id}/episodes").json() == []

    def test_reset_empty_returns_zero(self, client, db_session):
        item = _make_item(db_session)
        resp = client.delete(f"/watchlist/{item.id}/episodes?confirm=true")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    def test_reset_returns_404_for_unknown_watchlist(self, client):
        assert client.delete("/watchlist/99999/episodes?confirm=true").status_code == 404

    def test_reset_only_affects_target_watchlist(self, client, db_session):
        item_a = _make_item(db_session, title="Show A")
        item_b = _make_item(db_session, title="Show B")
        _make_ep(db_session, item_a.id, season=1, episode=1)
        _make_ep(db_session, item_b.id, season=1, episode=1)

        client.delete(f"/watchlist/{item_a.id}/episodes?confirm=true")

        assert len(client.get(f"/watchlist/{item_b.id}/episodes").json()) == 1
