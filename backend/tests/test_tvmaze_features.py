"""Tests for show metadata, episode guide, calendar, manual grab and browse (all TVMaze-backed)."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Download, DownloadHistory, QualityProfile, Setting, WatchlistEpisode, WatchlistItem,
)
from app.services import browse, calendar_view, episode_guide, show_metadata, tvmaze
from app.services.grab import grab_episode

NOW = datetime.now(timezone.utc)


def at(**delta) -> datetime:
    return NOW + timedelta(**delta)


def raw_ep(season, number, when, name=None, type_="regular"):
    return {"season": season, "number": number, "name": name or f"Ep {number}",
            "type": type_, "airstamp": when.isoformat() if when else None, "airdate": None}


def tv_show(id=1, name="Severance", status="Running", **kw):
    base = {
        "id": id, "name": name, "status": status, "premiered": "2022-02-18",
        "image": {"medium": "http://static.tvmaze.com/uploads/images/medium_portrait/1/1.jpg"},
        "summary": "<p>Mark leads a team whose <b>memories</b> are split.&nbsp;Ominous.</p>",
        "network": {"name": "Apple TV+"}, "genres": ["Drama"], "rating": {"average": 8.7},
        "type": "Scripted", "weight": 90,
    }
    base.update(kw)
    return base


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
def _fresh_caches():
    episode_guide.clear_cache()
    browse.clear_cache()
    yield
    episode_guide.clear_cache()
    browse.clear_cache()


def _item(db, **kw) -> WatchlistItem:
    base = dict(title="Severance", search_query="Severance", season=1, episode=1, tvmaze_id=10)
    base.update(kw)
    w = WatchlistItem(**base)
    db.add(w)
    db.commit()
    return w


def episodes_patch(by_id: dict):
    """Patch the raw TVMaze episode fetch; ids not in ``by_id`` are 'unreachable'."""
    return patch("app.services.tvmaze.get_episodes",
                 new=AsyncMock(side_effect=lambda tid: by_id.get(tid)))


# ── show_metadata ─────────────────────────────────────────────────────────────


class TestShowMetadata:
    def test_clean_summary_strips_html_and_entities(self):
        out = show_metadata.clean_summary("<p>Hello <b>world</b>.&nbsp;Fine &amp; dandy.</p>")
        assert out == "Hello world. Fine & dandy."

    def test_clean_summary_keeps_inline_tags_tight_but_separates_paragraphs(self):
        assert show_metadata.clean_summary("About <i>Severance</i>, a show.") == "About Severance, a show."
        assert show_metadata.clean_summary("<p>One.</p><p>Two.</p>") == "One. Two."
        assert show_metadata.clean_summary("Line one<br>Line two<br/>Line three") == "Line one Line two Line three"

    def test_clean_summary_truncates_and_handles_empty(self):
        assert show_metadata.clean_summary(None) is None
        assert show_metadata.clean_summary("<p> </p>") is None
        long = show_metadata.clean_summary("word " * 400, limit=50)
        assert len(long) == 50 and long.endswith("…")

    def test_poster_prefers_medium_and_upgrades_http(self):
        assert show_metadata.poster_url(tv_show()).startswith("https://static.tvmaze.com/")
        assert show_metadata.poster_url({"image": {"original": "https://x/y.jpg"}}) == "https://x/y.jpg"
        assert show_metadata.poster_url({"image": None}) is None
        assert show_metadata.poster_url({}) is None

    def test_network_falls_back_to_web_channel(self):
        assert show_metadata.network_name({"network": None, "webChannel": {"name": "Netflix"}}) == "Netflix"
        assert show_metadata.network_name({}) is None

    @pytest.mark.parametrize("name,expected", [
        ("The Office (US)", "The Office"), ("Doctor Who (2005)", "Doctor Who"),
        ("Shameless (UK)", "Shameless"), ("Plain Name", "Plain Name"),
        ("Fringe (Pilot Special)", "Fringe (Pilot Special)"),
    ])
    def test_search_query_from_name(self, name, expected):
        assert show_metadata.search_query_from_name(name) == expected

    def test_apply_show_sets_fields_and_keeps_existing_when_missing(self, db):
        item = _item(db, tvmaze_id=None, poster_url="https://old/poster.jpg", network="Old Net")
        show_metadata.apply_show(item, tv_show(image=None, network=None))
        assert item.tvmaze_id == 1 and item.show_status == "Running"
        assert item.poster_url == "https://old/poster.jpg"   # not wiped by a missing image
        assert item.network == "Old Net"
        assert item.premiered == "2022-02-18" and "memories" in item.summary

    def test_apply_show_ignores_unknown_status(self, db):
        item = _item(db, show_status="Ended")
        show_metadata.apply_show(item, tv_show(status="Weird"))
        assert item.show_status == "Ended"


class TestEnrichItem:
    def _run(self, item):
        return asyncio.run(show_metadata.enrich_item(item))

    def test_uses_cached_tvmaze_id_first(self, db):
        item = _item(db, tvmaze_id=5)
        with patch("app.services.tvmaze.fetch_show", new=AsyncMock(return_value=tv_show(id=5))) as g, \
             patch("app.services.tvmaze.lookup_by_name", new=AsyncMock()) as s:
            assert self._run(item) == show_metadata.FOUND
        g.assert_awaited_once_with(5)
        s.assert_not_awaited()
        assert item.metadata_checked_at is not None and item.metadata_attempts == 0

    def test_falls_back_to_search(self, db):
        item = _item(db, tvmaze_id=None)
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(return_value=tv_show(id=7))):
            assert self._run(item) == show_metadata.FOUND
        assert item.tvmaze_id == 7

    def test_unknown_show_is_recorded_and_counted(self, db):
        item = _item(db, tvmaze_id=None)
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(return_value=None)):
            assert self._run(item) == show_metadata.NOT_FOUND
        assert item.metadata_checked_at is not None and item.poster_url is None
        assert item.metadata_attempts == 1

    def test_tvmaze_being_unavailable_is_not_remembered_as_not_found(self, db):
        """The bug: a rate-limited lookup used to be stamped 'checked' and never retried."""
        item = _item(db, tvmaze_id=None)
        unavailable = AsyncMock(side_effect=tvmaze.TVMazeUnavailable("HTTP 429"))
        with patch("app.services.tvmaze.lookup_by_name", new=unavailable):
            assert self._run(item) == show_metadata.UNAVAILABLE
        assert item.metadata_checked_at is None and (item.metadata_attempts or 0) == 0

    def test_unexpected_errors_are_swallowed_and_not_remembered(self, db):
        item = _item(db, tvmaze_id=None)
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert self._run(item) == show_metadata.UNAVAILABLE
        assert item.metadata_checked_at is None


class TestStartingPoint:
    def _eps(self):
        return [
            {"season": 1, "number": n, "name": "", "airs_at": at(days=-100 + n)} for n in (1, 2)
        ] + [
            {"season": 2, "number": 1, "name": "", "airs_at": at(days=-1)},
            {"season": 2, "number": 2, "name": "", "airs_at": at(days=6)},
            {"season": 2, "number": 3, "name": "", "airs_at": at(days=13)},
        ]

    def test_next_is_first_unaired(self):
        assert show_metadata.starting_point(self._eps(), "Running", "next") == (2, 2)

    def test_beginning(self):
        assert show_metadata.starting_point(self._eps(), "Running", "beginning") == (1, 1)

    def test_default_depends_on_status(self):
        assert show_metadata.starting_point(self._eps(), "Running", None) == (2, 2)
        assert show_metadata.starting_point(self._eps(), "Ended", None) == (1, 1)

    def test_everything_aired_means_next_season(self):
        eps = [e for e in self._eps() if e["airs_at"] < NOW]
        assert show_metadata.starting_point(eps, "Running", "next") == (3, 1)

    def test_unknown_episodes(self):
        assert show_metadata.starting_point(None, "Running", "next") == (1, 1)
        assert show_metadata.starting_point([], "Running", None) == (1, 1)


# ── guide + calendar ──────────────────────────────────────────────────────────


class TestGuide:
    def test_statuses_counts_and_pointer(self, db):
        w = _item(db, season=1, episode=3)
        db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=1, torrent_name="Sev.S01E01"))
        db.commit()
        raw = [raw_ep(1, 1, at(days=-30)), raw_ep(1, 2, at(days=-20)), raw_ep(1, 3, at(days=3)),
               raw_ep(0, 1, at(days=-50), type_="significant_special")]
        with episodes_patch({10: raw}):
            g = asyncio.run(calendar_view.build_guide(db, w))
        assert g["available"] and g["pointer"] == {"season": 1, "episode": 3}
        (s1,) = g["seasons"]
        assert [e["status"] for e in s1["episodes"]] == ["downloaded", "missing", "upcoming"]
        assert (s1["total"], s1["aired"], s1["downloaded"]) == (3, 2, 1)
        assert s1["episodes"][0]["torrent_name"] == "Sev.S01E01"
        assert s1["episodes"][2]["airs_at"].endswith("Z")

    def test_unavailable_without_data(self, db):
        w = _item(db, tvmaze_id=None)
        g = asyncio.run(calendar_view.build_guide(db, w))
        assert g["available"] is False and g["seasons"] == []

    def test_episode_with_no_air_date_is_upcoming(self, db):
        w = _item(db)
        with episodes_patch({10: [raw_ep(1, 1, None)]}):
            g = asyncio.run(calendar_view.build_guide(db, w))
        assert g["seasons"][0]["episodes"][0]["status"] == "upcoming"


class TestCalendar:
    def test_window_sort_status_and_unmatched(self, db):
        a = _item(db, title="Alpha", tvmaze_id=1, poster_url="https://p/a.jpg")
        b = _item(db, title="Beta", tvmaze_id=2, enabled=False)
        _item(db, title="NoMatch", tvmaze_id=None)
        _item(db, title="Down", tvmaze_id=3)           # TVMaze unreachable for this one
        db.add(WatchlistEpisode(watchlist_id=a.id, season=1, episode=1))
        db.commit()
        raw = {
            1: [raw_ep(1, 1, at(days=-2)), raw_ep(1, 2, at(days=-1)), raw_ep(1, 3, at(days=5)),
                raw_ep(1, 4, at(days=200)), raw_ep(1, 0 + 9, at(days=-40))],
            2: [raw_ep(2, 1, at(days=1))],
        }
        with episodes_patch(raw):
            out = asyncio.run(calendar_view.build_calendar(db, now=NOW))
        got = [(e["title"], e["season"], e["episode"], e["status"]) for e in out["entries"]]
        assert got == [
            ("Alpha", 1, 1, "downloaded"),
            ("Alpha", 1, 2, "missing"),
            ("Beta", 2, 1, "upcoming"),
            ("Alpha", 1, 3, "upcoming"),
        ]
        assert out["unmatched"] == 2   # no TVMaze id + unreachable
        first = out["entries"][0]
        assert first["poster_url"] == "https://p/a.jpg" and first["watchlist_id"] == a.id
        assert [e["enabled"] for e in out["entries"] if e["title"] == "Beta"] == [False]
        assert b.id  # sanity

    def test_custom_window(self, db):
        _item(db, tvmaze_id=1)
        raw = {1: [raw_ep(1, 1, at(days=-3)), raw_ep(1, 2, at(days=10))]}
        with episodes_patch(raw):
            out = asyncio.run(calendar_view.build_calendar(db, now=NOW, days_back=1, days_ahead=5))
        assert out["entries"] == []


# ── manual episode grab ───────────────────────────────────────────────────────


def _result(title="Severance S01E02 1080p x265", h="a", seeds=20):
    return {"title": title, "seeds": seeds, "size_bytes": 5, "source": "tpb",
            "magnet": "magnet:?xt=urn:btih:" + h * 40, "info_hash": h * 40}


@pytest.fixture()
def grabbing(Session):
    ctx = {"adds": [], "results": [_result()]}

    def fake_add(magnet, save_path, category=None):
        ctx["adds"].append((magnet, save_path))
        return magnet[-40:]

    with patch("app.services.indexers.search_all", side_effect=lambda *a, **k: [dict(r) for r in ctx["results"]]), \
         patch("app.services.qbittorrent.add_torrent", side_effect=fake_add), \
         patch("app.services.apprise_notify.notify") as notify:
        ctx["notify"] = notify
        yield ctx


class TestGrabEpisode:
    def test_started_records_everything(self, db, grabbing):
        w = _item(db, quality="1080p", codec="x265", season=1, episode=5)
        out = grab_episode(db, w, 1, 2)
        assert out["status"] == "started" and out["title"].startswith("Severance S01E02")
        assert db.query(Download).one().watchlist_id == w.id
        assert db.query(DownloadHistory).one().source == "watchlist"
        assert db.query(WatchlistEpisode).filter_by(season=1, episode=2).count() == 1
        db.refresh(w)
        assert (w.season, w.episode) == (1, 5)          # pointer untouched when it wasn't this episode
        grabbing["notify"].assert_called_once()

    def test_advances_pointer_when_it_was_next_in_line(self, db, grabbing):
        w = _item(db, quality="1080p", codec="x265", season=1, episode=2)
        grab_episode(db, w, 1, 2)
        db.refresh(w)
        assert (w.season, w.episode) == (1, 3)

    def test_not_found(self, db, grabbing):
        grabbing["results"] = []
        w = _item(db)
        out = grab_episode(db, w, 1, 2)
        assert out["status"] == "not_found" and db.query(Download).count() == 0

    def test_already_downloaded(self, db, grabbing):
        w = _item(db)
        db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=2))
        db.commit()
        assert grab_episode(db, w, 1, 2)["status"] == "already_downloaded"
        assert grabbing["adds"] == []

    def test_duplicate_is_reported_not_added(self, db, grabbing):
        w = _item(db)
        with patch("app.services.duplicate.check_duplicate",
                   return_value={"is_duplicate": True, "matched_name": "x", "match_type": "hash"}):
            assert grab_episode(db, w, 1, 2)["status"] == "duplicate"
        assert grabbing["adds"] == []

    def test_skips_a_duplicate_and_takes_next_best(self, db, grabbing):
        grabbing["results"] = [_result(h="a", seeds=50), _result("Severance S01E02 720p", h="b", seeds=10)]
        w = _item(db, quality="Any", codec="Any")

        def dup(torrent_hash, torrent_name, db):
            return {"is_duplicate": torrent_hash == "a" * 40, "matched_name": "x", "match_type": "hash"}
        with patch("app.services.duplicate.check_duplicate", side_effect=dup):
            out = grab_episode(db, w, 1, 2)
        assert out["status"] == "started" and out["title"].endswith("720p")

    def test_qbittorrent_failure_propagates_and_records_nothing(self, db, grabbing):
        w = _item(db)
        with patch("app.services.qbittorrent.add_torrent", side_effect=RuntimeError("qbit down")):
            with pytest.raises(RuntimeError):
                grab_episode(db, w, 1, 2)
        assert db.query(Download).count() == 0 and db.query(WatchlistEpisode).count() == 0

    def test_uses_profile_and_item_download_path(self, db, grabbing):
        from app.models import DownloadPath
        dp = DownloadPath(name="TV", path="/mnt/tv", is_default=False)
        db.add(dp)
        db.commit()
        w = _item(db, download_path_id=dp.id)
        grab_episode(db, w, 1, 2)
        assert grabbing["adds"][0][1] == "/mnt/tv"
        assert db.query(DownloadHistory).one().folder == "TV"


# ── browse ────────────────────────────────────────────────────────────────────


def sched_ep(show, season=1, number=1, when=None):
    return {"season": season, "number": number, "airstamp": (when or at(days=1)).isoformat(),
            "_embedded": {"show": show}}


class TestBrowse:
    def test_aggregate_dedupes_sorts_filters(self):
        eps = [
            sched_ep(tv_show(1, "Quiet", weight=10), 1, 3, at(days=2)),
            sched_ep(tv_show(2, "Popular", weight=95), 4, 1, at(days=3)),
            sched_ep(tv_show(2, "Popular", weight=95), 4, 2, at(days=1)),
            sched_ep(tv_show(3, "Late Show", type="Talk Show", weight=99), 1, 1),
            sched_ep(tv_show(4, "Sports", type="Sports", weight=98), 1, 1),
            {"season": 1, "number": 1, "airstamp": at(days=1).isoformat()},   # no show embedded
        ]
        cards = browse.aggregate_schedule(eps, NOW)
        assert [c["name"] for c in cards] == ["Popular", "Quiet"]
        pop = cards[0]
        assert pop["next_episode"] == "S04E02"               # earliest upcoming airing
        assert pop["season_premiere"] is True                # S04E01 airs this week
        assert cards[1]["season_premiere"] is False
        assert pop["poster_url"].startswith("https://") and pop["network"] == "Apple TV+"

    def test_recently_aired_counts_but_old_does_not_beat_upcoming(self):
        eps = [sched_ep(tv_show(1), 1, 1, at(days=-3)), sched_ep(tv_show(1), 1, 2, at(days=2))]
        assert browse.aggregate_schedule(eps, NOW)[0]["next_episode"] == "S01E02"

    def test_airing_shows_fetches_both_schedules_per_day_and_caches(self):
        calls = []

        async def fake(day, country, web=False):
            calls.append((day, country, web))
            return [sched_ep(tv_show(1 if not web else 2, "W" if web else "B"))]
        today = date(2026, 1, 1)
        with patch("app.services.tvmaze.get_schedule", side_effect=fake):
            first = asyncio.run(browse.airing_shows("gb", days=3, today=today))
            again = asyncio.run(browse.airing_shows("GB", days=3, today=today))
        assert len(calls) == 6 and {c[1] for c in calls} == {"GB"}
        assert sum(1 for c in calls if c[2]) == 3
        assert {c["name"] for c in first} == {"W", "B"} and again == first

    def test_partial_failure_ok_total_failure_is_none(self):
        async def half(day, country, web=False):
            return None if web else [sched_ep(tv_show(1))]
        with patch("app.services.tvmaze.get_schedule", side_effect=half):
            assert len(asyncio.run(browse.airing_shows("US", days=2, today=date(2026, 1, 1)))) == 1
        browse.clear_cache()
        with patch("app.services.tvmaze.get_schedule", new=AsyncMock(return_value=None)):
            assert asyncio.run(browse.airing_shows("US", days=2, today=date(2026, 1, 1))) is None

    def test_country_validation(self):
        assert browse.valid_country("ca") == "CA"
        assert browse.valid_country("USA") == "US" and browse.valid_country(None) == "US"
        assert browse.valid_country("1!") == "US"


# ── HTTP endpoints ────────────────────────────────────────────────────────────


@pytest.fixture()
def api(db):
    from app.routers import browse as browse_router, calendar as cal_router, watchlist
    app = FastAPI()
    for r in (watchlist.router, cal_router.router, browse_router.router):
        app.include_router(r, prefix="/api")

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


class TestWatchlistEndpoints:
    def test_create_enriches_with_poster(self, api):
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(return_value=tv_show(id=42))):
            r = api.post("/api/watchlist", json={"title": "Severance", "search_query": "Severance"})
        body = r.json()
        assert r.status_code == 201
        assert body["tvmaze_id"] == 42 and body["poster_url"].startswith("https://")
        assert body["network"] == "Apple TV+" and body["metadata_checked_at"]

    def test_create_survives_lookup_failure(self, api):
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(side_effect=RuntimeError("down"))):
            r = api.post("/api/watchlist", json={"title": "X", "search_query": "X"})
        assert r.status_code == 201 and r.json()["poster_url"] is None
        assert r.json()["metadata_checked_at"] is None   # will be retried

    def test_create_survives_timeout(self, api):
        async def slow(_q):
            await asyncio.sleep(5)
        with patch("app.routers.watchlist._ENRICH_TIMEOUT", 0.05), \
             patch("app.services.tvmaze.lookup_by_name", new=slow):
            r = api.post("/api/watchlist", json={"title": "X", "search_query": "X"})
        assert r.status_code == 201
        assert r.json()["metadata_checked_at"] is None   # will be retried by refresh

    def test_refresh_metadata_only_touches_shows_that_are_due(self, api, db):
        has_poster = _item(db, title="Done", tvmaze_id=1, poster_url="https://p/x.jpg", metadata_checked_at=NOW)
        a = _item(db, title="A", search_query="A", tvmaze_id=None)
        b = _item(db, title="B", search_query="B", tvmaze_id=None)
        just_checked = _item(db, title="C", search_query="C", tvmaze_id=None,
                             metadata_checked_at=datetime.now(timezone.utc), metadata_attempts=1)

        async def search(q):
            return tv_show(id=9, name=q) if q == "A" else None
        with patch("app.routers.watchlist._REFRESH_PACING", 0), \
             patch("app.services.tvmaze.lookup_by_name", new=search):
            r = api.post("/api/watchlist/refresh-metadata")
        assert r.json() == {"checked": 2, "matched": 1, "remaining": 0, "unavailable": False}
        db.expire_all()
        assert db.get(WatchlistItem, a.id).poster_url
        assert db.get(WatchlistItem, b.id).metadata_checked_at is not None      # answered: not found
        assert db.get(WatchlistItem, has_poster.id).poster_url == "https://p/x.jpg"
        assert db.get(WatchlistItem, just_checked.id).metadata_attempts == 1    # backing off, untouched

    def test_guide_endpoint(self, api, db):
        w = _item(db)
        with episodes_patch({10: [raw_ep(1, 1, at(days=-5))]}):
            g = api.get(f"/api/watchlist/{w.id}/guide").json()
        assert g["available"] and g["seasons"][0]["episodes"][0]["status"] == "missing"
        assert api.get("/api/watchlist/999/guide").status_code == 404

    def test_guide_tries_to_match_show_once_if_unmatched(self, api, db):
        w = _item(db, tvmaze_id=None)
        with patch("app.services.tvmaze.lookup_by_name", new=AsyncMock(return_value=tv_show(id=10))), \
             episodes_patch({10: [raw_ep(1, 1, at(days=-5))]}):
            g = api.get(f"/api/watchlist/{w.id}/guide").json()
        assert g["available"] is True
        db.expire_all()
        assert db.get(WatchlistItem, w.id).tvmaze_id == 10

    def test_search_episode_endpoint(self, api, db, grabbing):
        w = _item(db, quality="1080p", codec="x265")
        r = api.post(f"/api/watchlist/{w.id}/episodes/1/2/search")
        assert r.status_code == 200 and r.json()["status"] == "started"
        assert api.post("/api/watchlist/999/episodes/1/2/search").status_code == 404
        assert api.post(f"/api/watchlist/{w.id}/episodes/1/0/search").status_code == 422

    def test_search_episode_reports_qbit_failure_as_502(self, api, db, grabbing):
        w = _item(db, quality="1080p", codec="x265")
        with patch("app.services.qbittorrent.add_torrent", side_effect=RuntimeError("qbit down")):
            r = api.post(f"/api/watchlist/{w.id}/episodes/1/2/search")
        assert r.status_code == 502 and "qbit down" in r.json()["detail"]


class TestAddFromTvmaze:
    EPS = [raw_ep(1, 1, at(days=-300)), raw_ep(1, 2, at(days=-290)),
           raw_ep(2, 1, at(days=-2)), raw_ep(2, 2, at(days=5))]

    def _post(self, api, **body):
        body.setdefault("tvmaze_id", 77)
        with patch("app.services.tvmaze.get_show", new=AsyncMock(return_value=tv_show(id=77, name="The Office (US)"))), \
             episodes_patch({77: self.EPS}):
            return api.post("/api/watchlist/from-tvmaze", json=body)

    def test_default_for_running_show_starts_at_next_episode(self, api):
        r = self._post(api)
        b = r.json()
        assert r.status_code == 201 and (b["season"], b["episode"]) == (2, 2)
        assert b["title"] == "The Office (US)" and b["search_query"] == "The Office"
        assert b["poster_url"] and b["tvmaze_id"] == 77 and b["metadata_checked_at"]

    def test_beginning(self, api):
        b = self._post(api, start="beginning").json()
        assert (b["season"], b["episode"]) == (1, 1)

    def test_invalid_start_rejected(self, api):
        assert self._post(api, start="whenever").status_code == 422

    def test_duplicate_is_409(self, api, db):
        _item(db, tvmaze_id=77)
        assert self._post(api).status_code == 409

    def test_unknown_show_is_404(self, api):
        with patch("app.services.tvmaze.get_show", new=AsyncMock(return_value=None)):
            r = api.post("/api/watchlist/from-tvmaze", json={"tvmaze_id": 5})
        assert r.status_code == 404

    def test_applies_default_profile(self, api, db):
        p = QualityProfile(name="P", quality="4K", codec="x265")
        db.add(p)
        db.commit()
        db.add(Setting(key="default_profile_id", value=str(p.id)))
        db.commit()
        assert self._post(api).json()["profile_id"] == p.id

    def test_stale_default_profile_is_ignored(self, api, db):
        db.add(Setting(key="default_profile_id", value="999"))
        db.commit()
        assert self._post(api).json()["profile_id"] is None

    def test_explicit_missing_profile_is_422(self, api):
        assert self._post(api, profile_id=999).status_code == 422


class TestCalendarAndBrowseEndpoints:
    def test_calendar_shape_and_validation(self, api, db):
        _item(db, tvmaze_id=1)
        with episodes_patch({1: [raw_ep(1, 1, at(days=2))]}):
            r = api.get("/api/calendar")
        assert r.status_code == 200 and len(r.json()["entries"]) == 1 and r.json()["unmatched"] == 0
        assert api.get("/api/calendar?days=0").status_code == 422
        assert api.get("/api/calendar?back=99").status_code == 422

    def test_browse_airing_marks_watchlist_membership(self, api, db):
        _item(db, title="Popular", tvmaze_id=2)
        _item(db, title="The Quiet One", tvmaze_id=None)   # matched by name
        db.add(Setting(key="browse_country", value="ca"))
        db.commit()
        seen = []

        async def sched(day, country, web=False):
            seen.append(country)
            return [] if web else [
                sched_ep(tv_show(2, "Popular", weight=90)),
                sched_ep(tv_show(3, "The Quiet One", weight=50)),
                sched_ep(tv_show(4, "Fresh", weight=10)),
            ]
        with patch("app.services.tvmaze.get_schedule", side_effect=sched):
            body = api.get("/api/browse").json()
        assert body["mode"] == "airing" and body["country"] == "CA" and set(seen) == {"CA"}
        flags = {s["name"]: s["on_watchlist"] for s in body["shows"]}
        assert flags == {"Popular": True, "The Quiet One": True, "Fresh": False}
        assert [s["name"] for s in body["shows"]][0] == "Popular"

    def test_browse_search(self, api):
        with patch("app.services.tvmaze.search_shows",
                   new=AsyncMock(return_value=[tv_show(1, "Severance"), tv_show(2, "Severance Docs")])):
            body = api.get("/api/browse?q=sever").json()
        assert body["mode"] == "search" and [s["name"] for s in body["shows"]] == ["Severance", "Severance Docs"]

    def test_browse_short_query_does_nothing(self, api):
        with patch("app.services.tvmaze.search_shows", new=AsyncMock()) as s:
            assert api.get("/api/browse?q=a").json()["shows"] == []
        s.assert_not_awaited()

    def test_browse_502_when_tvmaze_unreachable(self, api):
        with patch("app.services.tvmaze.get_schedule", new=AsyncMock(return_value=None)):
            r = api.get("/api/browse")
        assert r.status_code == 502 and "TVMaze" in r.json()["detail"]


class TestMigrationAddsMetadataColumns:
    def test_old_watchlist_gains_metadata_columns(self, tmp_path):
        import sqlite3
        from sqlalchemy import inspect
        from app.migrations import run_migrations

        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, title TEXT NOT NULL, search_query TEXT NOT NULL)")
        con.commit()
        con.close()
        engine = create_engine(f"sqlite:///{path}")
        run_migrations(bind=engine)
        cols = {c["name"] for c in inspect(engine).get_columns("watchlist")}
        assert {"poster_url", "summary", "network", "premiered", "metadata_checked_at"} <= cols
