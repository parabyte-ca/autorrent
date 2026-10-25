"""Tests for alerts, stall detection, the retry queue, disk space and candidates."""
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Download, DownloadHistory, DownloadPath, Setting, WatchlistEpisode, WatchlistItem,
)
from app.services import disk_space
from app.services.candidates import (
    failed_hashes, find_candidates, parse_episode_tag, result_hash,
)
from app.services.download_events import process_download_events


@pytest.fixture()
def Session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db(Session):
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def sweep(Session):
    """Run the sweep against the test DB with qBittorrent and alerts mocked."""
    qbit: dict = {"statuses": {}, "removed": [], "added": []}

    def fake_add(magnet, save_path, category=None):
        qbit["added"].append(magnet)
        return "newhash" + str(len(qbit["added"]))

    with patch("app.database.SessionLocal", Session), \
         patch("app.services.scheduler._sync_download_statuses"), \
         patch("app.services.qbittorrent.get_all_torrent_statuses",
               side_effect=lambda: qbit["statuses"]), \
         patch("app.services.qbittorrent.remove_torrent",
               side_effect=lambda h, delete_files=False: qbit["removed"].append((h, delete_files))), \
         patch("app.services.qbittorrent.add_torrent", side_effect=fake_add), \
         patch("app.services.apprise_notify.notify") as notify:
        qbit["notify"] = notify
        yield qbit


def _setting(db, **kv):
    for k, v in kv.items():
        db.add(Setting(key=k, value=v))
    db.commit()


def _dl(db, **kw) -> Download:
    base = dict(title="Show S01E02 1080p", torrent_hash="aaa111", status="downloading",
                download_path="/dl", qbit_removed=False)
    base.update(kw)
    d = Download(**base)
    db.add(d)
    db.commit()
    return d


def _watch(db, season=1, episode=3) -> WatchlistItem:
    w = WatchlistItem(title="Show", search_query="Show", season=season, episode=episode)
    db.add(w)
    db.commit()
    db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=2, torrent_hash="aaa111"))
    db.commit()
    return w


def _candidate(title="Show S01E02 720p", magnet="magnet:?xt=urn:btih:" + "b" * 40):
    return {"title": title, "magnet": magnet, "size_bytes": 1, "seeds": 9, "source": "tpb"}


# ── completion / failure alerts ───────────────────────────────────────────────


class TestAlerts:
    def test_complete_alert_fires_once(self, db, sweep):
        _setting(db, apprise_url="json://x")
        d = _dl(db, status="completed")
        process_download_events()
        process_download_events()
        assert sweep["notify"].call_count == 1
        assert "Show S01E02" in sweep["notify"].call_args.kwargs["body"]
        db.refresh(d)
        assert d.notified_complete is True

    def test_disabled_alert_does_not_notify_but_marks_flag(self, db, sweep):
        _setting(db, apprise_url="json://x", notify_on_complete="false")
        d = _dl(db, status="completed")
        process_download_events()
        sweep["notify"].assert_not_called()
        db.refresh(d)
        assert d.notified_complete is True  # enabling later must not flood old items

    def test_error_status_alerts_once_and_marks_failed(self, db, sweep):
        _setting(db, apprise_url="json://x")
        d = _dl(db, status="error", watchlist_id=None)
        db.add(DownloadHistory(name=d.title, source="manual", torrent_hash="aaa111",
                               status="downloading"))
        db.commit()
        process_download_events()
        process_download_events()
        assert sweep["notify"].call_count == 1
        db.refresh(d)
        assert d.status == "failed" and d.qbit_removed is True
        assert db.query(DownloadHistory).one().status == "failed"
        assert sweep["removed"] == [("aaa111", True)]

    def test_qbit_error_state_triggers_failure(self, db, sweep):
        d = _dl(db, watchlist_id=None)
        sweep["statuses"]["aaa111"] = {"status": "missingFiles", "progress": 0.4}
        process_download_events()
        db.refresh(d)
        assert d.status == "failed"
        assert "missingFiles" in sweep["notify"].call_args.kwargs["body"]


# ── stall detection ───────────────────────────────────────────────────────────


class TestStall:
    def test_first_sighting_starts_clock_not_stall(self, db, sweep):
        d = _dl(db, created_at=datetime.now(timezone.utc) - timedelta(hours=9))
        sweep["statuses"]["aaa111"] = {"status": "downloading", "progress": 0.1}
        process_download_events()
        db.refresh(d)
        assert d.status == "downloading"
        assert d.last_progress == pytest.approx(0.1)
        assert d.last_progress_at is not None

    def test_progress_resets_clock(self, db, sweep):
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        d = _dl(db, last_progress=0.1, last_progress_at=old)
        sweep["statuses"]["aaa111"] = {"status": "downloading", "progress": 0.3}
        process_download_events()
        db.refresh(d)
        assert d.status == "downloading"
        assert d.last_progress == pytest.approx(0.3)

    def test_no_progress_beyond_threshold_fails_and_removes(self, db, sweep):
        _setting(db, apprise_url="json://x")
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        d = _dl(db, last_progress=0.1, last_progress_at=old, watchlist_id=None)
        sweep["statuses"]["aaa111"] = {"status": "stalledDL", "progress": 0.1}
        process_download_events()
        db.refresh(d)
        assert d.status == "failed" and d.notified_stalled is True
        assert sweep["removed"] == [("aaa111", True)]
        assert sweep["notify"].call_count == 1  # no second "failed" alert
        assert "no progress" in sweep["notify"].call_args.kwargs["body"]

    @pytest.mark.parametrize("state", ["pausedDL", "queuedDL", "checkingDL"])
    def test_idle_by_design_states_never_stall(self, db, sweep, state):
        old = datetime.now(timezone.utc) - timedelta(hours=9)
        d = _dl(db, last_progress=0.0, last_progress_at=old)
        sweep["statuses"]["aaa111"] = {"status": state, "progress": 0.0}
        process_download_events()
        db.refresh(d)
        assert d.status == "downloading"

    def test_stall_hours_setting_respected(self, db, sweep):
        _setting(db, stall_hours="6")
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        d = _dl(db, last_progress=0.1, last_progress_at=old)
        sweep["statuses"]["aaa111"] = {"status": "stalledDL", "progress": 0.1}
        process_download_events()
        db.refresh(d)
        assert d.status == "downloading"


# ── retry queue ───────────────────────────────────────────────────────────────


class TestRetry:
    def _fail(self, db, sweep):
        sweep["statuses"]["aaa111"] = {"status": "error", "progress": 0.2}
        process_download_events()

    def test_retry_grabs_next_best_and_updates_records(self, db, sweep):
        _setting(db, apprise_url="json://x")
        w = _watch(db)
        _dl(db, watchlist_id=w.id)
        with patch("app.services.download_events.find_with_fallback",
                   return_value=[_candidate()]) as fc:
            self._fail(db, sweep)
        excluded = fc.call_args.args[4]  # (item, season, episode, min_seeds, hashes, titles)
        assert "aaa111" in excluded
        new = db.query(Download).filter(Download.status == "downloading").one()
        assert new.title == "Show S01E02 720p" and new.retry_count == 1
        assert new.watchlist_id == w.id and new.download_path == "/dl"
        ep = db.query(WatchlistEpisode).one()
        assert ep.torrent_name == "Show S01E02 720p" and ep.torrent_hash == new.torrent_hash
        assert "Retrying with" in sweep["notify"].call_args.kwargs["body"]

    def test_no_candidates_drops_episode_and_rolls_pointer_back(self, db, sweep):
        w = _watch(db, season=1, episode=3)
        _dl(db, watchlist_id=w.id)
        with patch("app.services.download_events.find_with_fallback", return_value=[]):
            self._fail(db, sweep)
        db.refresh(w)
        assert w.episode == 2  # scanner will look again next cycle
        assert db.query(WatchlistEpisode).count() == 0
        assert "will keep checking" in sweep["notify"].call_args.kwargs["body"]

    def test_gives_up_after_max_attempts(self, db, sweep):
        _setting(db, retry_max_attempts="2")
        w = _watch(db)
        _dl(db, watchlist_id=w.id, torrent_hash="old000", status="failed")
        _dl(db, watchlist_id=w.id)
        with patch("app.services.download_events.find_with_fallback") as fc:
            self._fail(db, sweep)
        fc.assert_not_called()
        db.refresh(w)
        assert w.episode == 3  # pointer NOT rolled back
        assert db.query(WatchlistEpisode).count() == 0
        assert "Gave up" in sweep["notify"].call_args.kwargs["body"]

    def test_retry_disabled(self, db, sweep):
        _setting(db, retry_enabled="false")
        w = _watch(db)
        _dl(db, watchlist_id=w.id)
        with patch("app.services.download_events.find_with_fallback") as fc:
            self._fail(db, sweep)
        fc.assert_not_called()
        assert db.query(Download).filter(Download.status == "downloading").count() == 0

    def test_manual_download_is_not_retried(self, db, sweep):
        _dl(db, watchlist_id=None)
        with patch("app.services.download_events.find_with_fallback") as fc:
            self._fail(db, sweep)
        fc.assert_not_called()

    def test_season_pack_is_not_retried(self, db, sweep):
        w = _watch(db)
        _dl(db, watchlist_id=w.id, title="Show S01 COMPLETE 1080p")
        with patch("app.services.download_events.find_with_fallback") as fc:
            self._fail(db, sweep)
        fc.assert_not_called()

    def test_add_failure_is_handled(self, db, sweep):
        w = _watch(db)
        _dl(db, watchlist_id=w.id)
        with patch("app.services.download_events.find_with_fallback", return_value=[_candidate()]), \
             patch("app.services.qbittorrent.add_torrent", side_effect=RuntimeError("boom")):
            self._fail(db, sweep)
        assert db.query(Download).filter(Download.status == "downloading").count() == 0
        assert "boom" in sweep["notify"].call_args.kwargs["body"]


# ── candidates ────────────────────────────────────────────────────────────────


class TestCandidates:
    ITEM = WatchlistItem(title="Show", search_query="Show", quality="1080p", codec="x265",
                         max_size_mb=None, preferred_groups=None)

    def _results(self):
        return [
            {"title": "Show S01E02 1080p x265 GRP", "seeds": 5, "size_bytes": 10,
             "magnet": "magnet:?xt=urn:btih:" + "a" * 40, "info_hash": "a" * 40},
            {"title": "Show S01E02 1080p x265 FLUX", "seeds": 2, "size_bytes": 10,
             "magnet": "magnet:?xt=urn:btih:" + "b" * 40, "info_hash": "b" * 40},
            {"title": "Other S01E02 1080p", "seeds": 50, "size_bytes": 10,
             "magnet": "magnet:?xt=urn:btih:" + "c" * 40, "info_hash": "c" * 40},
            {"title": "Show S01E05 1080p", "seeds": 50, "size_bytes": 10,
             "magnet": "magnet:?xt=urn:btih:" + "d" * 40, "info_hash": "d" * 40},
            {"title": "Show S01E02 1080p lowseed", "seeds": 1, "size_bytes": 10,
             "magnet": "magnet:?xt=urn:btih:" + "e" * 40, "info_hash": "e" * 40},
        ]

    def test_filters_and_ranks_by_seeds(self):
        with patch("app.services.indexers.search_all", return_value=self._results()):
            out = find_candidates(self.ITEM, 1, 2, min_seeds=2)
        # "Other" kept? it contains no "show" word → dropped; wrong episode dropped; low seed dropped
        assert [r["info_hash"] for r in out] == ["a" * 40, "b" * 40]

    def test_preferred_group_beats_seeds(self):
        item = WatchlistItem(title="Show", search_query="Show", quality="1080p", codec="x265",
                             max_size_mb=None, preferred_groups="flux")
        with patch("app.services.indexers.search_all", return_value=self._results()):
            out = find_candidates(item, 1, 2, min_seeds=2)
        assert out[0]["info_hash"] == "b" * 40

    def test_excluded_hashes_removed(self):
        with patch("app.services.indexers.search_all", return_value=self._results()):
            out = find_candidates(self.ITEM, 1, 2, 2, exclude_hashes={"a" * 40})
        assert [r["info_hash"] for r in out] == ["b" * 40]

    def test_max_size_filter(self):
        item = WatchlistItem(title="Show", search_query="Show", quality="1080p", codec="x265",
                             max_size_mb=1, preferred_groups=None)
        big = self._results()
        big[0]["size_bytes"] = 5 * 1024 * 1024
        with patch("app.services.indexers.search_all", return_value=big):
            out = find_candidates(item, 1, 2, 2)
        assert [r["info_hash"] for r in out] == ["b" * 40]

    def test_parse_episode_tag(self):
        assert parse_episode_tag("Foo.S03E11.1080p") == (3, 11)
        assert parse_episode_tag("Foo S03 Complete") is None

    def test_result_hash_falls_back_to_magnet(self):
        assert result_hash({"magnet": "magnet:?xt=urn:btih:" + "F" * 40}) == "f" * 40

    def test_failed_hashes(self, db):
        w = _watch(db)
        _dl(db, watchlist_id=w.id, torrent_hash="BAD1", status="failed")
        _dl(db, watchlist_id=w.id, torrent_hash="ok1", status="downloading")
        _dl(db, watchlist_id=w.id, torrent_hash="other", status="failed", title="Show S01E09")
        assert failed_hashes(db, w.id, 1, 2) == {"bad1"}


# ── disk space ────────────────────────────────────────────────────────────────

Usage = namedtuple("Usage", "total used free")


class TestDiskSpace:
    def test_threshold(self):
        assert disk_space.threshold_bytes({"low_disk_warn_gb": "10"}) == 10_000_000_000
        assert disk_space.threshold_bytes({"low_disk_warn_gb": "0"}) == 0
        assert disk_space.threshold_bytes({"low_disk_warn_gb": "abc"}) == 0
        assert disk_space.threshold_bytes({}) == 10_000_000_000

    def test_is_low(self):
        assert disk_space.is_low(1, 10) is True
        assert disk_space.is_low(10, 10) is False
        assert disk_space.is_low(None, 10) is False
        assert disk_space.is_low(1, 0) is False

    def test_alerts_once_per_day_then_again_after_recovery(self, db):
        db.add(DownloadPath(name="Media", path="/m", is_default=True))
        db.commit()
        settings = {"low_disk_warn_gb": "10", "apprise_url": "json://x"}
        with patch("app.services.apprise_notify.notify") as notify, \
             patch("shutil.disk_usage", return_value=Usage(100, 98, 2_000_000_000)):
            assert disk_space.check_and_alert(db, settings) == ["Media"]
            assert disk_space.check_and_alert(db, settings) == ["Media"]
            assert notify.call_count == 1
        with patch("app.services.apprise_notify.notify") as notify, \
             patch("shutil.disk_usage", return_value=Usage(100, 10, 90_000_000_000)):
            assert disk_space.check_and_alert(db, settings) == []
            notify.assert_not_called()
        with patch("app.services.apprise_notify.notify") as notify, \
             patch("shutil.disk_usage", return_value=Usage(100, 98, 1_000_000_000)):
            disk_space.check_and_alert(db, settings)
            assert notify.call_count == 1  # dropped again after recovering

    def test_disabled_returns_empty(self, db):
        db.add(DownloadPath(name="Media", path="/m"))
        db.commit()
        with patch("shutil.disk_usage", return_value=Usage(100, 99, 1)):
            assert disk_space.check_and_alert(db, {"low_disk_warn_gb": "0"}) == []
