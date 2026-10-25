"""Tests for the Trakt watchlist sync and the first-run wizard status."""
import asyncio
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
from app.models import (
    Download, DownloadPath, QualityProfile, Setting, TraktSeen, WatchlistItem,
)
from app.services import episode_guide, trakt, trakt_sync, tvmaze

CLIENT_ID = "a" * 64
NOW = datetime.now(timezone.utc)


# ── helpers ───────────────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def _clear_caches():
    episode_guide.clear_cache()
    yield
    episode_guide.clear_cache()


def mock_async_httpx(handler):
    real = httpx.AsyncClient
    return patch.object(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def entry(trakt_id, title, year=2020, imdb=None, tvdb=None, listed_at="2024-01-01T00:00:00.000Z", type_="show"):
    return {"rank": 1, "listed_at": listed_at, "type": type_,
            "show": {"title": title, "year": year,
                     "ids": {"trakt": trakt_id, "imdb": imdb, "tvdb": tvdb, "slug": title.lower()}}}


def norm_entry(trakt_id, title, year=2020, imdb=None, tvdb=None, listed_at="2024-01-01"):
    return {"trakt_id": trakt_id, "title": title, "year": year, "imdb": imdb,
            "tvdb": tvdb, "listed_at": listed_at}


def tv_show(id=1, name="Show", status="Running", premiered="2020-01-01"):
    return {"id": id, "name": name, "status": status, "premiered": premiered,
            "image": None, "summary": None, "network": None}


def set_setting(db, **kv):
    for k, v in kv.items():
        row = db.query(Setting).filter(Setting.key == k).first()
        if row:
            row.value = v
        else:
            db.add(Setting(key=k, value=v))
    db.commit()


def configured(db, **extra):
    set_setting(db, trakt_client_id=CLIENT_ID, trakt_username="someone", **extra)


# ── Trakt client ──────────────────────────────────────────────────────────────


def fetch(handler, client_id=CLIENT_ID, username="someone"):
    with mock_async_httpx(handler):
        return asyncio.run(trakt.fetch_watchlist_shows(client_id, username))


class TestTraktClient:
    def test_sends_headers_and_normalises_sorted_oldest_first(self):
        seen = {}

        def handler(req):
            seen["headers"], seen["path"], seen["params"] = req.headers, req.url.path, dict(req.url.params)
            return httpx.Response(200, json=[
                entry(2, "Newer", listed_at="2024-05-01T00:00:00Z"),
                entry(1, "Older", listed_at="2023-01-01T00:00:00Z", imdb="tt1", tvdb=7),
                entry(3, "A Movie", type_="movie"),
                {"type": "show", "show": {"title": "No ids", "ids": {}}},
                {"type": "show"},
            ])
        out = fetch(handler)
        assert [s["title"] for s in out] == ["Older", "Newer"]
        assert out[0] == {"trakt_id": 1, "title": "Older", "year": 2020, "imdb": "tt1",
                          "tvdb": 7, "listed_at": "2023-01-01T00:00:00Z"}
        assert seen["headers"]["trakt-api-key"] == CLIENT_ID
        assert seen["headers"]["trakt-api-version"] == "2"
        assert seen["path"] == "/users/someone/watchlist/shows"
        assert seen["params"] == {"page": "1", "limit": "100"}

    def test_follows_pagination(self):
        def handler(req):
            page = int(req.url.params["page"])
            return httpx.Response(200, json=[entry(page, f"S{page}")],
                                  headers={"x-pagination-page-count": "3"})
        assert [s["trakt_id"] for s in fetch(handler)] == [1, 2, 3]

    def test_stops_at_page_cap(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(200, json=[entry(len(calls), "x")],
                                  headers={"x-pagination-page-count": "999"})
        fetch(handler)
        assert len(calls) == trakt._MAX_PAGES

    @pytest.mark.parametrize("status,fragment", [
        (404, "was not found"), (401, "public"), (403, "client ID"), (500, "HTTP 500"),
    ])
    def test_http_errors_become_friendly_messages(self, status, fragment):
        with pytest.raises(trakt.TraktError, match=fragment):
            fetch(lambda r: httpx.Response(status))

    def test_rate_limit_retried_once(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "0"}) if len(calls) == 1 \
                else httpx.Response(200, json=[entry(1, "x")])
        with patch("app.services.trakt.asyncio.sleep", new=AsyncMock()):
            assert len(fetch(handler)) == 1
        assert len(calls) == 2

    def test_persistent_rate_limit_is_an_error(self):
        with patch("app.services.trakt.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(trakt.TraktError, match="429"):
                fetch(lambda r: httpx.Response(429, headers={"retry-after": "0"}))

    def test_network_failure(self):
        def handler(req):
            raise httpx.ConnectError("down")
        with pytest.raises(trakt.TraktError, match="Could not reach Trakt"):
            fetch(handler)

    @pytest.mark.parametrize("response", [
        httpx.Response(200, text="<html>"), httpx.Response(200, json={"not": "a list"}),
    ])
    def test_bad_bodies(self, response):
        with pytest.raises(trakt.TraktError):
            fetch(lambda r: response)

    @pytest.mark.parametrize("client_id,username", [
        ("short", "someone"), ("a" * 64, ""), ("a" * 64, "../etc"), ("a" * 64, "a b"),
        ("a" * 64 + "\n", "someone"), ("a" * 64, "someone\n"),
    ])
    def test_invalid_input_rejected_without_a_request(self, client_id, username):
        def handler(req):
            raise AssertionError("must not call Trakt")
        with pytest.raises(trakt.TraktError):
            fetch(handler, client_id, username)


# ── TVMaze id lookup ──────────────────────────────────────────────────────────


class TestLookupShow:
    def _lookup(self, handler, **kw):
        with mock_async_httpx(handler):
            return asyncio.run(tvmaze.lookup_show(**kw))

    def test_by_imdb_and_thetvdb(self):
        seen = []

        def handler(req):
            seen.append(dict(req.url.params))
            return httpx.Response(200, json={"id": 5, "name": "X"})
        assert self._lookup(handler, imdb="tt1")["id"] == 5
        assert self._lookup(handler, thetvdb=81189)["id"] == 5
        assert seen == [{"imdb": "tt1"}, {"thetvdb": "81189"}]

    def test_unknown_or_error_is_none(self):
        assert self._lookup(lambda r: httpx.Response(404), imdb="tt9") is None
        assert self._lookup(lambda r: httpx.Response(200, json={}), imdb="tt9") is None

        def boom(req):
            raise httpx.ConnectError("x")
        assert self._lookup(boom, imdb="tt9") is None

    def test_no_ids_makes_no_request(self):
        def handler(req):
            raise AssertionError("no request expected")
        assert self._lookup(handler) is None


# ── resolving a Trakt entry to a TVMaze show ──────────────────────────────────


class TestResolveShow:
    def _resolve(self, entry_, imdb=None, tvdb=None, search=None):
        with patch("app.services.tvmaze.lookup_show",
                   new=AsyncMock(side_effect=lambda imdb=None, thetvdb=None: imdb_r if imdb else tvdb_r)) as lk, \
             patch("app.services.tvmaze.search_show", new=AsyncMock(return_value=search)) as sr:
            return asyncio.run(trakt_sync.resolve_show(entry_)), lk, sr

    def test_imdb_first(self):
        global imdb_r, tvdb_r
        imdb_r, tvdb_r = tv_show(1), tv_show(2)
        show, lk, sr = self._resolve(norm_entry(1, "S", imdb="tt1", tvdb=9))
        assert show["id"] == 1 and lk.await_count == 1 and sr.await_count == 0

    def test_falls_back_to_tvdb_then_search(self):
        global imdb_r, tvdb_r
        imdb_r, tvdb_r = None, tv_show(2)
        show, lk, _ = self._resolve(norm_entry(1, "S", imdb="tt1", tvdb=9))
        assert show["id"] == 2 and lk.await_count == 2
        imdb_r, tvdb_r = None, None
        show, _, sr = self._resolve(norm_entry(1, "Show", year=2020), search=tv_show(3, "Show", premiered="2020-05-01"))
        assert show["id"] == 3 and sr.await_count == 1

    def test_name_search_rejects_a_remake_with_the_same_title(self):
        global imdb_r, tvdb_r
        imdb_r = tvdb_r = None
        show, _, _ = self._resolve(norm_entry(1, "Shameless", year=2004),
                                   search=tv_show(3, "Shameless", premiered="2011-01-09"))
        assert show is None

    def test_year_tolerance_of_one(self):
        global imdb_r, tvdb_r
        imdb_r = tvdb_r = None
        show, _, _ = self._resolve(norm_entry(1, "Show", year=2019),
                                   search=tv_show(3, "Show", premiered="2020-01-01"))
        assert show is not None

    def test_unknown_year_requires_exact_title(self):
        global imdb_r, tvdb_r
        imdb_r = tvdb_r = None
        ok, _, _ = self._resolve(norm_entry(1, "The Show!", year=None),
                                 search=tv_show(3, "the show", premiered=None))
        bad, _, _ = self._resolve(norm_entry(1, "The Show", year=None),
                                  search=tv_show(3, "The Show Returns", premiered=None))
        assert ok is not None and bad is None


# ── the sync ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def world():
    """Fake Trakt list + TVMaze: ``world['list']`` entries, ``world['shows']`` by trakt id."""
    ctx = {"list": [], "shows": {}, "error": None}

    async def fake_fetch(client_id, username):
        if ctx["error"]:
            raise trakt.TraktError(ctx["error"])
        return list(ctx["list"])

    async def fake_resolve(entry_):
        return ctx["shows"].get(entry_["trakt_id"])

    async def fake_episodes(tvmaze_id):
        return [
            {"season": 1, "number": 1, "type": "regular", "name": "", "airstamp": (NOW - timedelta(days=30)).isoformat()},
            {"season": 1, "number": 2, "type": "regular", "name": "", "airstamp": (NOW + timedelta(days=3)).isoformat()},
        ]

    with patch("app.services.trakt_sync.fetch_watchlist_shows", side_effect=fake_fetch), \
         patch("app.services.trakt_sync.resolve_show", side_effect=fake_resolve), \
         patch("app.services.tvmaze.get_episodes", side_effect=fake_episodes), \
         patch("app.services.apprise_notify.notify") as notify:
        ctx["notify"] = notify
        yield ctx


def run_sync(db, now=None):
    return asyncio.run(trakt_sync.sync(db, now=now))


class TestSync:
    def test_adds_new_show_at_next_episode_and_records_everything(self, db, world):
        configured(db, apprise_url="json://x")
        world["list"] = [norm_entry(10, "Severance")]
        world["shows"] = {10: tv_show(77, "Severance")}
        r = run_sync(db)
        assert r["ok"] and r["added"] == ["Severance"] and r["checked"] == 1
        item = db.query(WatchlistItem).one()
        assert (item.tvmaze_id, item.season, item.episode) == (77, 1, 2)   # next unaired
        assert db.query(TraktSeen).one().status == "added"
        world["notify"].assert_called_once()
        assert "Severance" in world["notify"].call_args.kwargs["body"]
        assert trakt_sync.last_result(db)["added"] == ["Severance"]

    def test_start_setting_beginning(self, db, world):
        configured(db, trakt_start="beginning")
        world["list"] = [norm_entry(10, "S")]
        world["shows"] = {10: tv_show(77)}
        run_sync(db)
        item = db.query(WatchlistItem).one()
        assert (item.season, item.episode) == (1, 1)

    def test_default_profile_applied(self, db, world):
        p = QualityProfile(name="P", quality="4K", codec="x265")
        db.add(p)
        db.commit()
        configured(db, default_profile_id=str(p.id))
        world["list"] = [norm_entry(10, "S")]
        world["shows"] = {10: tv_show(77)}
        run_sync(db)
        assert db.query(WatchlistItem).one().profile_id == p.id

    def test_show_already_on_watchlist_is_not_duplicated(self, db, world):
        configured(db)
        db.add(WatchlistItem(title="Severance", search_query="Severance", tvmaze_id=77))
        db.add(WatchlistItem(title="By Name", search_query="By Name"))
        db.commit()
        world["list"] = [norm_entry(10, "Severance"), norm_entry(11, "by name!")]
        world["shows"] = {10: tv_show(77, "Severance"), 11: tv_show(88, "By Name")}
        r = run_sync(db)
        assert r["added"] == [] and r["existing"] == 2
        assert db.query(WatchlistItem).count() == 2
        world["notify"].assert_not_called()

    def test_removed_show_is_not_re_added(self, db, world):
        configured(db)
        world["list"] = [norm_entry(10, "Severance")]
        world["shows"] = {10: tv_show(77, "Severance")}
        run_sync(db)
        db.query(WatchlistItem).delete()
        db.commit()
        r = run_sync(db)
        assert r["added"] == [] and db.query(WatchlistItem).count() == 0

    def test_forgetting_history_allows_re_adding(self, db, world):
        configured(db)
        world["list"] = [norm_entry(10, "S")]
        world["shows"] = {10: tv_show(77)}
        run_sync(db)
        db.query(WatchlistItem).delete()
        db.query(TraktSeen).delete()
        db.commit()
        assert run_sync(db)["added"] == ["Show"]

    def test_unmatched_retried_only_after_a_week(self, db, world):
        configured(db)
        world["list"] = [norm_entry(10, "Obscure")]
        r = run_sync(db, now=NOW)
        assert r["unmatched"] == ["Obscure"] and db.query(TraktSeen).one().status == "unmatched"

        world["shows"] = {10: tv_show(77, "Obscure")}   # TVMaze learns about it
        assert run_sync(db, now=NOW + timedelta(days=3))["added"] == []
        r = run_sync(db, now=NOW + timedelta(days=8))
        assert r["added"] == ["Obscure"] and db.query(TraktSeen).one().status == "added"

    def test_cap_defers_the_rest_to_the_next_run(self, db, world):
        configured(db)
        n = trakt_sync.MAX_NEW_PER_RUN + 3
        world["list"] = [norm_entry(i, f"Show {i}", listed_at=f"2024-01-{i:02d}") for i in range(1, n + 1)]
        world["shows"] = {i: tv_show(100 + i, f"Show {i}") for i in range(1, n + 1)}
        r = run_sync(db)
        assert len(r["added"]) == trakt_sync.MAX_NEW_PER_RUN and r["deferred"] == 3
        assert r["added"][0] == "Show 1"                              # oldest first
        assert db.query(TraktSeen).count() == trakt_sync.MAX_NEW_PER_RUN   # deferred ones not recorded
        r2 = run_sync(db)
        assert len(r2["added"]) == 3 and r2["deferred"] == 0
        assert db.query(WatchlistItem).count() == n
        assert "more waiting" in world["notify"].call_args_list[0].kwargs["body"]

    def test_trakt_error_is_reported_and_changes_nothing(self, db, world):
        configured(db)
        world["error"] = "Trakt user 'someone' was not found."
        r = run_sync(db)
        assert r["ok"] is False and "not found" in r["error"]
        assert db.query(WatchlistItem).count() == 0
        assert trakt_sync.last_result(db)["error"] == r["error"]

    def test_race_with_existing_item_is_treated_as_existing(self, db, world):
        configured(db)
        world["list"] = [norm_entry(10, "S")]
        world["shows"] = {10: tv_show(77)}
        with patch("app.services.watchlist_add.create_from_tvmaze",
                   new=AsyncMock(side_effect=trakt_sync.watchlist_add.AlreadyOnWatchlist())):
            r = run_sync(db)
        assert r["existing"] == 1 and r["added"] == []


# ── HTTP API ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(db):
    from app.routers import onboarding, trakt as trakt_router
    app = FastAPI()
    app.include_router(trakt_router.router, prefix="/api")
    app.include_router(onboarding.router, prefix="/api")

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


class TestTraktApi:
    def test_status_before_and_after_setup(self, api, db, world):
        s = api.get("/api/trakt/status").json()
        assert s["configured"] is False and s["enabled"] is False and s["last_result"] is None
        configured(db, trakt_enabled="true")
        s = api.get("/api/trakt/status").json()
        assert s["configured"] is True and s["enabled"] is True
        assert s["max_new_per_run"] == trakt_sync.MAX_NEW_PER_RUN

    def test_sync_requires_configuration(self, api):
        r = api.post("/api/trakt/sync")
        assert r.status_code == 400 and "client ID" in r.json()["detail"]

    def test_sync_runs_and_status_shows_result(self, api, db, world):
        configured(db)
        world["list"] = [norm_entry(10, "S")]
        world["shows"] = {10: tv_show(77)}
        assert api.post("/api/trakt/sync").json()["added"] == ["Show"]
        s = api.get("/api/trakt/status").json()
        assert s["last_result"]["added"] == ["Show"] and s["seen_count"] == 1

    def test_sync_reports_trakt_errors_in_the_body(self, api, db, world):
        configured(db)
        world["error"] = "Trakt refused the request."
        r = api.post("/api/trakt/sync")
        assert r.status_code == 200 and r.json()["ok"] is False

    def test_test_endpoint(self, api):
        async def fetch_ok(cid, user):
            return [norm_entry(1, "a"), norm_entry(2, "b")]

        async def fetch_bad(cid, user):
            raise trakt.TraktError("nope")
        with patch("app.services.trakt.fetch_watchlist_shows", side_effect=fetch_ok):
            assert api.post("/api/trakt/test", json={"client_id": CLIENT_ID, "username": "u"}).json() == {"ok": True, "shows": 2}
        with patch("app.services.trakt.fetch_watchlist_shows", side_effect=fetch_bad):
            assert api.post("/api/trakt/test", json={"client_id": CLIENT_ID, "username": "u"}).json() == {"ok": False, "error": "nope"}
        r = api.post("/api/trakt/test", json={"client_id": "", "username": ""}).json()
        assert r["ok"] is False and "Enter both" in r["error"]

    def test_forget_history(self, api, db):
        db.add(TraktSeen(trakt_id=1, title="x", status="added"))
        db.commit()
        assert api.delete("/api/trakt/seen").json() == {"ok": True, "cleared": 1}
        assert db.query(TraktSeen).count() == 0


class TestScheduledSync:
    def _run(self, Session):
        from app.services.scheduler import run_trakt_sync
        with patch("app.database.SessionLocal", Session), \
             patch("app.services.trakt_sync.sync", new=AsyncMock()) as sync:
            run_trakt_sync()
        return sync

    def test_skipped_when_disabled_or_unconfigured(self, db, Session):
        assert self._run(Session).await_count == 0
        set_setting(db, trakt_enabled="true")
        assert self._run(Session).await_count == 0          # enabled but no credentials
        set_setting(db, trakt_enabled="false", trakt_client_id=CLIENT_ID, trakt_username="u")
        assert self._run(Session).await_count == 0          # credentials but switched off

    def test_runs_when_enabled_and_configured(self, db, Session):
        configured(db, trakt_enabled="true")
        assert self._run(Session).await_count == 1

    def test_job_is_registered(self):
        from app.services import scheduler
        with patch.object(scheduler._scheduler, "start"), \
             patch.object(scheduler._scheduler, "add_job") as add:
            scheduler.start_scheduler()
        assert "trakt_sync" in [c.kwargs.get("id") for c in add.call_args_list]


# ── first-run wizard status ───────────────────────────────────────────────────


class TestOnboardingStatus:
    def test_needed_on_a_fresh_install(self, api):
        assert api.get("/api/onboarding/status").json() == {"needed": True}

    @pytest.mark.parametrize("make", [
        lambda db: db.add(WatchlistItem(title="x", search_query="x")),
        lambda db: db.add(Download(title="x")),
        lambda db: db.add(DownloadPath(name="n", path="/p")),
        lambda db: db.add(Setting(key="qbit_password", value="secret")),
    ])
    def test_not_needed_for_an_install_already_in_use(self, api, db, make):
        make(db)
        db.commit()
        assert api.get("/api/onboarding/status").json() == {"needed": False}

    def test_empty_qbit_password_does_not_count(self, api, db):
        db.add(Setting(key="qbit_password", value=""))
        db.commit()
        assert api.get("/api/onboarding/status").json()["needed"] is True

    def test_complete_hides_it_for_good_and_is_idempotent(self, api):
        assert api.post("/api/onboarding/complete").json() == {"ok": True}
        assert api.post("/api/onboarding/complete").json() == {"ok": True}
        assert api.get("/api/onboarding/status").json() == {"needed": False}
