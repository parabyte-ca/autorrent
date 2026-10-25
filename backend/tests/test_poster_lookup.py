"""Poster lookup robustness: title cleaning, year-aware matching, and never
remembering a TVMaze outage (or rate limit) as "this show has no poster"."""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import WatchlistItem
from app.services import show_metadata, tvmaze

NOW = datetime.now(timezone.utc)


def show(id_, name, premiered="2020-01-01", image=True):
    return {"id": id_, "name": name, "status": "Running", "premiered": premiered,
            "image": {"medium": f"https://static.tvmaze.com/{id_}.jpg"} if image else None,
            "summary": None, "network": None}


@pytest.fixture()
def Session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db(Session):
    s = Session()
    yield s
    s.close()


def add(db, title, **kw):
    item = WatchlistItem(title=title, search_query=kw.pop("search_query", title), **kw)
    db.add(item)
    db.commit()
    return item


# ── title cleaning ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("Severance", ("Severance", None)),
    ("War 2026", ("War", 2026)),
    ("Doctor Who (2005)", ("Doctor Who", 2005)),
    ("Best.Pancakes.in.the.Country.2025.1080p.WEB-DL.x265-GRP", ("Best Pancakes in the Country", 2025)),
    ("Some.Show.S01E02.1080p.WEB.h264", ("Some Show", None)),
    ("Some Show S02 COMPLETE 720p", ("Some Show", None)),
    ("Wild horse nine", ("Wild horse nine", None)),
    ("[GRP] Anime Title - 01 [1080p]", ("Anime Title - 01", None)),
    ("The_Show_Name_2019_BluRay", ("The Show Name", 2019)),
    ("1883", ("1883", None)),                       # a leading number is the name, not a year
    ("2012 Documentary", ("2012 Documentary", None)),
    ("Marvel's Agents of S.H.I.E.L.D.", ("Marvel's Agents of S.H.I.E.L.D.", None)),
    ("Show Name 4K UHD", ("Show Name", None)),
    ("", ("", None)),
])
def test_parse_title(text, expected):
    assert show_metadata.parse_title(text) == expected


def test_a_token_inside_a_word_is_not_a_release_tag():
    # "Shows1080" / "Games2160" style words must not be chopped
    assert show_metadata.parse_title("Hass1080 Stories") == ("Hass1080 Stories", None)


# ── finding the right show ────────────────────────────────────────────────────


def run(coro):
    return asyncio.run(coro)


class TestFindShow:
    def test_year_in_title_picks_the_show_that_premiered_then(self):
        many = AsyncMock(return_value=[show(1, "War", "2018-05-01"), show(2, "War", "2026-02-01")])
        with patch("app.services.tvmaze.lookup_many", new=many), \
             patch("app.services.tvmaze.lookup_by_name", new=AsyncMock()) as single:
            found = run(show_metadata.find_show(["War 2026"]))
        assert found["id"] == 2
        many.assert_awaited_once_with("War")
        single.assert_not_awaited()          # never guess with fuzzy search when a year was given

    def test_year_that_matches_nothing_gives_no_match_rather_than_a_wrong_show(self):
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[show(1, "War", "2018-05-01")])):
            assert run(show_metadata.find_show(["War 2026"])) is None

    def test_year_tolerance_of_one(self):
        with patch("app.services.tvmaze.lookup_many", new=AsyncMock(return_value=[show(1, "X", "2025-12-01")])):
            assert run(show_metadata.find_show(["X 2026"]))["id"] == 1

    def test_release_style_name_is_cleaned_before_searching(self):
        single = AsyncMock(return_value=show(3, "Best Pancakes in the Country"))
        with patch("app.services.tvmaze.lookup_by_name", new=single):
            found = run(show_metadata.find_show(["Some.Show.Name.S01E01.1080p.WEB-DL"]))
        single.assert_awaited_once_with("Some Show Name")
        assert found["id"] == 3

    def test_falls_through_to_the_title_when_the_query_finds_nothing(self):
        async def lookup(name):
            return show(5, "Wild Horse Nine") if name == "Wild horse nine" else None
        with patch("app.services.tvmaze.lookup_by_name", new=lookup):
            found = run(show_metadata.find_show(["Wild Horse 9 Extras 1080p", "Wild horse nine"]))
        assert found["id"] == 5

    def test_identical_candidates_are_only_searched_once(self):
        single = AsyncMock(return_value=None)
        with patch("app.services.tvmaze.lookup_by_name", new=single):
            assert run(show_metadata.find_show(["Foo", "Foo", "  ", None, "foo"])) is None
        assert single.await_count == 1

    def test_unavailable_propagates_so_the_caller_can_retry_later(self):
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(side_effect=tvmaze.TVMazeUnavailable("x"))):
            with pytest.raises(tvmaze.TVMazeUnavailable):
                run(show_metadata.find_show(["Foo"]))


# ── retry back-off ────────────────────────────────────────────────────────────


class TestMetadataDue:
    def _item(self, db, **kw):
        return add(db, "X", **kw)

    def test_never_checked_is_due(self, db):
        assert show_metadata.metadata_due(self._item(db))

    def test_shows_with_a_poster_are_never_due(self, db):
        assert not show_metadata.metadata_due(self._item(db, poster_url="https://p/x.jpg"))

    @pytest.mark.parametrize("attempts,wait_days", [(1, 1), (2, 2), (3, 4), (4, 7), (9, 7)])
    def test_backoff_grows_then_caps_at_a_week(self, db, attempts, wait_days):
        checked = NOW - timedelta(days=wait_days)
        item = self._item(db, metadata_checked_at=checked, metadata_attempts=attempts)
        assert show_metadata.metadata_due(item, NOW + timedelta(minutes=1))
        assert not show_metadata.metadata_due(item, checked + timedelta(days=wait_days) - timedelta(minutes=1))

    def test_naive_timestamps_from_sqlite_are_handled(self, db):
        item = self._item(db, metadata_checked_at=(NOW - timedelta(days=3)).replace(tzinfo=None), metadata_attempts=1)
        assert show_metadata.metadata_due(item)


# ── strict TVMaze lookups ─────────────────────────────────────────────────────


def mock_async_httpx(handler):
    real = httpx.AsyncClient
    return patch.object(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


class TestStrictLookups:
    def _get(self, handler, fn, *args):
        with mock_async_httpx(handler), patch("app.services.tvmaze.asyncio.sleep", new=AsyncMock()):
            return asyncio.run(fn(*args))

    def test_found(self):
        out = self._get(lambda r: httpx.Response(200, json=show(1, "X")), tvmaze.lookup_by_name, "X")
        assert out["id"] == 1

    def test_genuine_404_is_none(self):
        assert self._get(lambda r: httpx.Response(404), tvmaze.lookup_by_name, "X") is None
        assert self._get(lambda r: httpx.Response(404), tvmaze.fetch_show, 5) is None

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_rate_limit_and_server_errors_are_unavailable_not_not_found(self, status):
        with pytest.raises(tvmaze.TVMazeUnavailable):
            self._get(lambda r: httpx.Response(status, headers={"retry-after": "0"}), tvmaze.lookup_by_name, "X")

    def test_network_failure_is_unavailable(self):
        def boom(req):
            raise httpx.ConnectError("down")
        with pytest.raises(tvmaze.TVMazeUnavailable):
            self._get(boom, tvmaze.fetch_show, 5)

    def test_garbage_body_is_unavailable(self):
        with pytest.raises(tvmaze.TVMazeUnavailable):
            self._get(lambda r: httpx.Response(200, text="<html>"), tvmaze.lookup_by_name, "X")

    def test_429_is_retried_after_the_advertised_delay(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "2"}) if len(calls) == 1 \
                else httpx.Response(200, json=show(1, "X"))
        with mock_async_httpx(handler), patch("app.services.tvmaze.asyncio.sleep", new=AsyncMock()) as sleep:
            out = asyncio.run(tvmaze.lookup_by_name("X"))
        assert out["id"] == 1 and len(calls) == 2
        sleep.assert_awaited_once_with(2.0)

    def test_lookup_many_returns_shows_in_order(self):
        body = [{"score": 1, "show": show(1, "A")}, {"score": 0.5, "show": show(2, "B")}, {"score": 0}]
        out = self._get(lambda r: httpx.Response(200, json=body), tvmaze.lookup_many, "x")
        assert [s["id"] for s in out] == [1, 2]

    def test_lenient_helpers_still_return_none_on_failure(self):
        """Other callers (status checks, Trakt) rely on the old forgiving behaviour."""
        with mock_async_httpx(lambda r: httpx.Response(500)):
            assert asyncio.run(tvmaze.search_show("X")) is None
            assert asyncio.run(tvmaze.get_show(1)) is None


# ── the refresh endpoint under a rate limit ───────────────────────────────────


@pytest.fixture()
def api(db):
    from app.routers import watchlist
    app = FastAPI()
    app.include_router(watchlist.router, prefix="/api")

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


class TestRefreshUnderRateLimit:
    def test_shows_after_the_limit_hits_stay_due_and_get_posters_on_the_next_call(self, api, db):
        """The reported bug: with many shows, the later ones got HTTP 429, were stamped
        'checked' and never got a poster."""
        items = [add(db, f"Show {i:02d}") for i in range(1, 9)]
        budget = {"calls": 5}      # TVMaze answers 5 lookups, then rate-limits

        async def lookup(name):
            if budget["calls"] <= 0:
                raise tvmaze.TVMazeUnavailable("HTTP 429")
            budget["calls"] -= 1
            return show(100 + int(name.split()[-1]), name)

        with patch("app.routers.watchlist._REFRESH_PACING", 0), \
             patch("app.services.tvmaze.lookup_by_name", new=lookup):
            first = api.post("/api/watchlist/refresh-metadata").json()
        assert first == {"checked": 5, "matched": 5, "remaining": 3, "unavailable": True}
        db.expire_all()
        have = [db.get(WatchlistItem, i.id) for i in items]
        assert [bool(h.poster_url) for h in have] == [True] * 5 + [False] * 3
        assert all(h.metadata_checked_at is None for h in have[5:]), "outage must not be remembered"

        budget["calls"] = 100       # the limit window passes
        with patch("app.routers.watchlist._REFRESH_PACING", 0), \
             patch("app.services.tvmaze.lookup_by_name", new=lookup):
            second = api.post("/api/watchlist/refresh-metadata").json()
        assert second == {"checked": 3, "matched": 3, "remaining": 0, "unavailable": False}
        db.expire_all()
        assert all(db.get(WatchlistItem, i.id).poster_url for i in items)

    def test_stops_at_the_first_sign_of_trouble_instead_of_hammering(self, api, db):
        for i in range(6):
            add(db, f"S{i}")
        lookup = AsyncMock(side_effect=tvmaze.TVMazeUnavailable("down"))
        with patch("app.routers.watchlist._REFRESH_PACING", 0), patch("app.services.tvmaze.lookup_by_name", new=lookup):
            body = api.post("/api/watchlist/refresh-metadata").json()
        assert lookup.await_count == 1
        assert body["unavailable"] is True and body["checked"] == 0 and body["remaining"] == 6

    def test_requests_are_paced(self, api, db):
        for i in range(3):
            add(db, f"S{i}")
        with patch("app.routers.watchlist.asyncio.sleep", new=AsyncMock()) as sleep, \
             patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(return_value=None)):
            api.post("/api/watchlist/refresh-metadata")
        assert sleep.await_count == 2            # between shows, not before the first

    def test_a_show_tvmaze_does_not_know_backs_off_instead_of_retrying_every_load(self, api, db):
        item = add(db, "Totally Unknown")
        lookup = AsyncMock(return_value=None)
        with patch("app.routers.watchlist._REFRESH_PACING", 0), patch("app.services.tvmaze.lookup_by_name", new=lookup):
            assert api.post("/api/watchlist/refresh-metadata").json()["checked"] == 1
            assert api.post("/api/watchlist/refresh-metadata").json() == \
                {"checked": 0, "matched": 0, "remaining": 0, "unavailable": False}
        assert lookup.await_count == 1
        db.expire_all()
        assert db.get(WatchlistItem, item.id).metadata_attempts == 1

    def test_cleaned_title_finds_a_show_the_literal_title_missed(self, api, db):
        item = add(db, "Best.Pancakes.in.the.Country.2025.1080p.WEB-DL", search_query="Best.Pancakes.in.the.Country.2025.1080p.WEB-DL")
        many = AsyncMock(return_value=[show(7, "Best Pancakes in the Country", "2025-06-01")])
        with patch("app.routers.watchlist._REFRESH_PACING", 0), patch("app.services.tvmaze.lookup_many", new=many):
            body = api.post("/api/watchlist/refresh-metadata").json()
        assert body["matched"] == 1
        many.assert_awaited_once_with("Best Pancakes in the Country")
        db.expire_all()
        assert db.get(WatchlistItem, item.id).tvmaze_id == 7


# ── one-time migration ────────────────────────────────────────────────────────


def test_migration_clears_the_stale_checked_stamps_exactly_once(tmp_path):
    from app.migrations import run_migrations

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE watchlist (id INTEGER PRIMARY KEY, title TEXT NOT NULL, search_query TEXT NOT NULL,
            poster_url TEXT, metadata_checked_at DATETIME);
        INSERT INTO watchlist VALUES (1, 'no poster', 'no poster', NULL, '2026-10-25 12:00:00');
        INSERT INTO watchlist VALUES (2, 'has poster', 'has poster', 'https://p/x.jpg', '2026-10-25 12:00:00');
    """)
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{path}")
    run_migrations(bind=engine)
    with engine.connect() as c:
        rows = {r[0]: r[1] for r in c.exec_driver_sql("select id, metadata_checked_at from watchlist").fetchall()}
    assert rows[1] is None, "shows without a poster get a fresh lookup"
    assert rows[2] is not None, "shows that already have one are left alone"

    with engine.begin() as c:        # a later, legitimate stamp must survive further startups
        c.exec_driver_sql("update watchlist set metadata_checked_at = '2026-10-26 08:00:00' where id = 1")
    run_migrations(bind=engine)
    with engine.connect() as c:
        assert c.exec_driver_sql("select metadata_checked_at from watchlist where id = 1").scalar() is not None
