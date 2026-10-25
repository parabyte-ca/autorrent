"""Tests for Prowlarr/torrent resolution, season packs, episode guide and quality profiles."""
import hashlib
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import (
    Download, QualityProfile, Setting, WatchlistEpisode, WatchlistItem,
)
from app.services import episode_guide, torrent_meta
from app.services.candidates import effective_item, find_with_fallback
from app.services.indexers import jackett
from app.services.scheduler import scan_watchlist


# ── helpers ───────────────────────────────────────────────────────────────────


def bencode(v) -> bytes:
    if isinstance(v, int):
        return b"i" + str(v).encode() + b"e"
    if isinstance(v, bytes):
        return str(len(v)).encode() + b":" + v
    if isinstance(v, str):
        return bencode(v.encode())
    if isinstance(v, list):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        return b"d" + b"".join(bencode(k) + bencode(v[k]) for k in sorted(v)) + b"e"
    raise TypeError(v)


INFO = {"name": "Show.S01.1080p", "piece length": 16384, "pieces": b"x" * 20, "length": 123}
TORRENT = bencode({
    "announce": "http://tracker.example/announce",
    "announce-list": [["http://tracker.example/announce"], ["udp://t2.example:80"]],
    "info": INFO,
})
INFO_HASH = hashlib.sha1(bencode(INFO)).hexdigest()


def mock_httpx(module, handler):
    """Make ``module.httpx.Client`` talk to ``handler`` instead of the network."""
    real = httpx.Client
    return patch.object(
        module.httpx, "Client",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


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
def _clear_guide_cache():
    episode_guide.clear_cache()
    yield
    episode_guide.clear_cache()


# ── torrent_meta ──────────────────────────────────────────────────────────────


class TestTorrentMeta:
    def test_magnet_has_correct_hash_name_and_trackers(self):
        magnet, h = torrent_meta.torrent_to_magnet(TORRENT)
        assert h == INFO_HASH
        assert f"btih:{INFO_HASH}" in magnet
        assert "dn=Show.S01.1080p" in magnet
        assert urllib.parse.quote("http://tracker.example/announce", safe="") in magnet
        assert urllib.parse.quote("udp://t2.example:80", safe="") in magnet
        assert magnet.count("tracker.example") == 1  # announce de-duplicated against the list

    def test_rejects_non_torrent(self):
        with pytest.raises(ValueError):
            torrent_meta.torrent_to_magnet(b"<html>nope</html>")

    def test_rejects_torrent_without_info(self):
        with pytest.raises(ValueError, match="info"):
            torrent_meta.torrent_to_magnet(bencode({"announce": "x"}))

    def test_url_returns_torrent_file(self):
        with mock_httpx(torrent_meta, lambda r: httpx.Response(200, content=TORRENT)):
            magnet, h = torrent_meta.url_to_magnet("http://prowlarr/dl/1.torrent")
        assert h == INFO_HASH and magnet.startswith("magnet:?xt=urn:btih:")

    def test_url_redirecting_to_magnet(self):
        m = "magnet:?xt=urn:btih:" + "ab" * 20
        with mock_httpx(torrent_meta, lambda r: httpx.Response(302, headers={"location": m})):
            magnet, h = torrent_meta.url_to_magnet("http://prowlarr/dl/1")
        assert magnet == m and h == "ab" * 20

    def test_url_follows_http_redirect_then_torrent(self):
        def handler(req):
            if req.url.path == "/dl/1":
                return httpx.Response(302, headers={"location": "/real.torrent"})
            return httpx.Response(200, content=TORRENT)
        with mock_httpx(torrent_meta, handler):
            _, h = torrent_meta.url_to_magnet("http://prowlarr/dl/1")
        assert h == INFO_HASH

    def test_url_http_error_raises(self):
        with mock_httpx(torrent_meta, lambda r: httpx.Response(404)):
            with pytest.raises(httpx.HTTPStatusError):
                torrent_meta.url_to_magnet("http://prowlarr/dl/1")

    def test_redirect_loop_gives_up(self):
        with mock_httpx(torrent_meta, lambda r: httpx.Response(302, headers={"location": "/again"})):
            with pytest.raises(ValueError, match="redirects"):
                torrent_meta.url_to_magnet("http://prowlarr/dl/1")

    def test_oversized_torrent_rejected(self):
        big = b"d" + b"x" * (torrent_meta.MAX_TORRENT_BYTES + 1)
        with mock_httpx(torrent_meta, lambda r: httpx.Response(200, content=big)):
            with pytest.raises(ValueError, match="large"):
                torrent_meta.url_to_magnet("http://prowlarr/dl/1")

    def test_add_torrent_resolves_urls_and_returns_hash(self):
        from app.services import qbittorrent
        client = MagicMock()
        with patch.object(qbittorrent, "_get_client", return_value=client), \
             patch.object(qbittorrent, "url_to_magnet",
                          return_value=("magnet:?xt=urn:btih:" + "cd" * 20, "cd" * 20)):
            h = qbittorrent.add_torrent("https://prowlarr/dl/9", "/dl", "cat")
        assert h == "cd" * 20
        assert client.torrents_add.call_args.kwargs["urls"].startswith("magnet:")

    def test_add_torrent_leaves_magnets_alone(self):
        from app.services import qbittorrent
        client = MagicMock()
        magnet = "magnet:?xt=urn:btih:" + "ef" * 20
        with patch.object(qbittorrent, "_get_client", return_value=client), \
             patch.object(qbittorrent, "url_to_magnet") as resolve:
            qbittorrent.add_torrent(magnet, "/dl")
        resolve.assert_not_called()


# ── Prowlarr / Jackett parsing and probing ────────────────────────────────────


def _prowlarr_items():
    return [
        {"title": "A", "magnetUrl": "magnet:?xt=urn:btih:" + "1" * 40, "seeders": 5,
         "leechers": 1, "size": 1 << 30, "indexer": "Pub", "infoHash": "1" * 40},
        {"title": "B private", "downloadUrl": "http://prowlarr/dl/b", "seeders": 9,
         "infoHash": "2" * 40, "indexer": "Priv"},
        {"title": "C hash only", "infoHash": "3" * 40, "seeders": 2, "indexer": "X"},
        {"title": "D nothing", "seeders": 50},
        {"title": "E guid magnet", "guid": "magnet:?xt=urn:btih:" + "5" * 40, "seeders": 1},
    ]


class TestProwlarrParsing:
    def _search(self, payload, url_status=200):
        def handler(req):
            if req.url.path == "/api/v1/search":
                return httpx.Response(url_status, json=payload)
            return httpx.Response(404)
        with mock_httpx(jackett, handler):
            return jackett.search_jackett("q", "http://p:9696", "key")

    def test_link_priority_and_skipping(self):
        out = {r["title"]: r for r in self._search(_prowlarr_items())}
        assert out["A"]["magnet"].startswith("magnet:")
        # private-tracker result keeps its .torrent URL rather than a useless bare hash
        assert out["B private"]["magnet"] == "http://prowlarr/dl/b"
        assert out["C hash only"]["magnet"].startswith("magnet:?xt=urn:btih:" + "3" * 40)
        assert out["E guid magnet"]["magnet"].startswith("magnet:")
        assert "D nothing" not in out
        assert out["A"]["source"] == "prowlarr/Pub"

    def test_jackett_fallback_shape(self):
        payload = {"Results": [{"Title": "J", "MagnetUri": "magnet:?xt=urn:btih:" + "7" * 40,
                                "Seeders": 4, "Tracker": "T", "Size": 10}]}

        def handler(req):
            if req.url.path == "/api/v1/search":
                return httpx.Response(404)
            return httpx.Response(200, json=payload)
        with mock_httpx(jackett, handler):
            out = jackett.search_jackett("q", "http://j:9117", "key")
        assert out[0]["source"] == "jackett/T" and out[0]["seeds"] == 4


class TestProbe:
    def _probe(self, handler):
        with mock_httpx(jackett, handler):
            return jackett.probe_jackett("http://x:9696/", "key")

    def test_prowlarr_ok_counts_enabled_indexers(self):
        def handler(req):
            if req.url.path == "/api/v1/system/status":
                assert req.headers["x-api-key"] == "key"
                return httpx.Response(200, json={"version": "1.2.3"})
            if req.url.path == "/api/v1/indexer":
                return httpx.Response(200, json=[{"enable": True}, {"enable": False}, {}])
            return httpx.Response(404)
        assert self._probe(handler) == {"ok": True, "kind": "Prowlarr", "version": "1.2.3", "indexers": 2}

    def test_bad_key_prowlarr(self):
        assert self._probe(lambda r: httpx.Response(401))["error"] == "Invalid API key."

    def test_jackett_ok(self):
        def handler(req):
            if req.url.path == "/api/v2.0/indexers":
                return httpx.Response(200, json=[{}, {}, {}])
            return httpx.Response(404)
        out = self._probe(handler)
        assert out["ok"] and out["kind"] == "Jackett" and out["indexers"] == 3

    def test_jackett_bad_key(self):
        def handler(req):
            return httpx.Response(401 if "v2.0" in req.url.path else 404)
        assert self._probe(handler)["error"] == "Invalid API key."

    def test_unreachable(self):
        def handler(req):
            raise httpx.ConnectError("down")
        out = self._probe(handler)
        assert out["ok"] is False and "Could not reach" in out["error"]

    def test_non_json_response(self):
        out = self._probe(lambda r: httpx.Response(200, text="<html>"))
        assert out["ok"] is False

    def test_endpoint_requires_both_fields(self):
        from app.routers.settings import router
        app = FastAPI()
        app.include_router(router)
        r = TestClient(app).post("/settings/test-jackett", json={"url": "", "api_key": ""})
        assert r.json()["ok"] is False


# ── episode guide ─────────────────────────────────────────────────────────────

NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


def _raw(season, number, airstamp, type_="regular"):
    return {"season": season, "number": number, "name": f"e{number}", "type": type_,
            "airstamp": airstamp.isoformat() if airstamp else None, "airdate": None}


def _aired(days_ago):
    return NOW - timedelta(days=days_ago)


class TestEpisodeGuide:
    def _patch(self, raw):
        return patch("app.services.tvmaze.get_episodes", new=AsyncMock(return_value=raw))

    def test_specials_and_unnumbered_are_ignored(self):
        raw = [_raw(1, 1, _aired(9)), _raw(1, None, _aired(9)),
               _raw(1, 2, _aired(8), "significant_special")]
        with self._patch(raw):
            eps = episode_guide.get_episodes(1)
        assert [e["number"] for e in eps] == [1]

    def test_season_complete_true_false_unknown(self):
        raw = [_raw(1, 1, _aired(9)), _raw(1, 2, _aired(2)), _raw(2, 1, NOW + timedelta(days=3))]
        with self._patch(raw):
            assert episode_guide.season_complete(1, 1, NOW) is True
            assert episode_guide.season_complete(1, 2, NOW) is False
            assert episode_guide.season_complete(1, 9, NOW) is None  # season not listed

    def test_unknown_when_no_id_or_fetch_failed(self):
        assert episode_guide.season_complete(None, 1) is None
        with patch("app.services.tvmaze.get_episodes", new=AsyncMock(return_value=None)):
            assert episode_guide.season_complete(5, 1) is None

    def test_missing_air_date_means_not_complete(self):
        with self._patch([_raw(1, 1, _aired(9)), _raw(1, 2, None)]):
            assert episode_guide.season_complete(1, 1, NOW) is False

    def test_aired_numbers(self):
        raw = [_raw(1, 1, _aired(9)), _raw(1, 2, _aired(1)), _raw(1, 3, NOW + timedelta(days=5))]
        with self._patch(raw):
            assert episode_guide.aired_episode_numbers(1, 1, NOW) == [1, 2]

    def test_episode_age(self):
        with self._patch([_raw(1, 1, NOW - timedelta(hours=30))]):
            assert episode_guide.episode_age_hours(1, 1, 1, NOW) == pytest.approx(30)
            assert episode_guide.episode_age_hours(1, 1, 9, NOW) is None

    def test_cached_and_failures_not_cached(self):
        fetch = AsyncMock(return_value=[_raw(1, 1, _aired(1))])
        with patch("app.services.tvmaze.get_episodes", new=fetch):
            episode_guide.get_episodes(7)
            episode_guide.get_episodes(7)
        assert fetch.await_count == 1

        episode_guide.clear_cache()
        flaky = AsyncMock(side_effect=[None, [_raw(1, 1, _aired(1))]])
        with patch("app.services.tvmaze.get_episodes", new=flaky):
            assert episode_guide.get_episodes(8) is None
            assert len(episode_guide.get_episodes(8)) == 1


# ── profiles: effective item + fallback ───────────────────────────────────────


def _item(db, **kw) -> WatchlistItem:
    base = dict(title="Show", search_query="Show", quality="720p", codec="x264",
                season=1, episode=1, tvmaze_id=111, prefer_season_packs=False)
    base.update(kw)
    w = WatchlistItem(**base)
    db.add(w)
    db.commit()
    return w


def _profile(db, **kw) -> QualityProfile:
    base = dict(name="P", quality="4K", codec="x265", max_size_mb=5000,
                preferred_groups="flux", prefer_season_packs=True)
    base.update(kw)
    p = QualityProfile(**base)
    db.add(p)
    db.commit()
    return p


class TestEffectiveItem:
    def test_item_without_profile_uses_own_fields(self, db):
        e = effective_item(db, _item(db))
        assert (e.quality, e.codec, e.prefer_season_packs, e.fallback_quality) == ("720p", "x264", False, None)

    def test_profile_overrides_item_fields(self, db):
        p = _profile(db, fallback_quality="1080p", fallback_after_hours=12)
        e = effective_item(db, _item(db, profile_id=p.id))
        assert (e.quality, e.codec, e.max_size_mb, e.preferred_groups) == ("4K", "x265", 5000, "flux")
        assert e.prefer_season_packs is True
        assert (e.fallback_quality, e.fallback_after_hours) == ("1080p", 12)

    def test_dangling_profile_id_falls_back_to_item(self, db):
        e = effective_item(db, _item(db, profile_id=999))
        assert e.quality == "720p"


def _res(title, seeds=10, h="a"):
    return {"title": title, "seeds": seeds, "size_bytes": 1,
            "magnet": "magnet:?xt=urn:btih:" + h * 40, "info_hash": h * 40, "source": "tpb"}


class TestFallback:
    def _eff(self, db, **kw):
        p = _profile(db, fallback_quality="1080p", fallback_after_hours=24, **kw)
        return effective_item(db, _item(db, profile_id=p.id))

    def _search(self, by_quality):
        calls = []

        def fake(query, quality=None, indexer="all", codec="x265", filter_adult=True):
            calls.append(quality)
            return by_quality.get(quality, [])
        return calls, patch("app.services.indexers.search_all", side_effect=fake)

    def test_primary_found_no_fallback(self, db):
        eff = self._eff(db)
        calls, p = self._search({"4K": [_res("Show S01E01 2160p")]})
        with p:
            out = find_with_fallback(eff, 1, 1, 1)
        assert len(out) == 1 and calls == ["4K"]

    def test_fallback_used_when_episode_old_enough(self, db):
        eff = self._eff(db)
        calls, p = self._search({"1080p": [_res("Show S01E01 1080p")]})
        with p, patch("app.services.episode_guide.episode_age_hours", return_value=48):
            out = find_with_fallback(eff, 1, 1, 1)
        assert [r["title"] for r in out] == ["Show S01E01 1080p"] and calls == ["4K", "1080p"]

    def test_no_fallback_when_too_new(self, db):
        eff = self._eff(db)
        calls, p = self._search({"1080p": [_res("Show S01E01 1080p")]})
        with p, patch("app.services.episode_guide.episode_age_hours", return_value=3):
            assert find_with_fallback(eff, 1, 1, 1) == []
        assert calls == ["4K"]

    def test_no_fallback_when_air_date_unknown(self, db):
        eff = self._eff(db)
        calls, p = self._search({"1080p": [_res("Show S01E01 1080p")]})
        with p, patch("app.services.episode_guide.episode_age_hours", return_value=None):
            assert find_with_fallback(eff, 1, 1, 1) == []
        assert calls == ["4K"]


# ── scanner: season packs and profiles end-to-end ─────────────────────────────


@pytest.fixture()
def scan(Session):
    """Run scan_watchlist against the test DB with every outside dependency mocked."""
    ctx = {"adds": [], "queries": [], "qualities": [], "tvmaze": {}, "results": {}}

    def fake_search(query, quality=None, indexer="all", codec="x265", filter_adult=True):
        ctx["queries"].append(query)
        ctx["qualities"].append(quality)
        for suffix, res in ctx["results"].items():
            if query.endswith(suffix):
                return [dict(r) for r in res]
        return []

    def fake_add(magnet, save_path, category=None):
        ctx["adds"].append(magnet)
        return magnet[-40:]

    def fake_episodes(tvmaze_id):
        return ctx["tvmaze"].get(tvmaze_id)

    with patch("app.database.SessionLocal", Session), \
         patch("app.services.indexers.search_all", side_effect=fake_search), \
         patch("app.services.qbittorrent.add_torrent", side_effect=fake_add), \
         patch("app.services.apprise_notify.notify"), \
         patch("app.services.scheduler._sync_download_statuses"), \
         patch("app.services.scheduler._cleanup_completed"), \
         patch("app.services.episode_guide.get_episodes", side_effect=fake_episodes):
        yield ctx


def _season_eps(season, n, aired=True):
    when = _aired(30) if aired else datetime.now(timezone.utc) + timedelta(days=30)
    return [{"season": season, "number": i, "name": "", "airs_at": when} for i in range(1, n + 1)]


PACK = _res("Show S01 COMPLETE 1080p x265", seeds=40, h="9")
EP1 = _res("Show S01E01 1080p x265", seeds=10, h="8")


class TestSeasonPackScan:
    def test_pack_downloaded_records_all_aired_episodes_and_advances_season(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True)
        scan["tvmaze"][111] = _season_eps(1, 3)
        scan["results"] = {" S01": [PACK], " S01E01": [EP1]}
        scan_watchlist()
        assert scan["adds"] == [PACK["magnet"]]
        db.expire_all()
        rows = db.query(WatchlistEpisode).filter_by(watchlist_id=w.id).order_by(WatchlistEpisode.episode).all()
        assert [(r.season, r.episode, r.torrent_hash) for r in rows] == [(1, n, "9" * 40) for n in (1, 2, 3)]
        db.refresh(w)
        assert (w.season, w.episode) == (2, 1)

    def test_partial_episodes_already_tracked_are_not_duplicated(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True, episode=3)
        db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=1, torrent_hash="old"))
        db.commit()
        scan["tvmaze"][111] = _season_eps(1, 3)
        scan["results"] = {" S01": [PACK], " S01E03": []}
        scan_watchlist()
        db.expire_all()
        by_ep = {r.episode: r.torrent_hash for r in db.query(WatchlistEpisode).filter_by(watchlist_id=w.id)}
        assert by_ep == {1: "old", 2: "9" * 40, 3: "9" * 40}

    def test_running_season_skips_pack_search_and_grabs_episode(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True)
        scan["tvmaze"][111] = _season_eps(1, 8, aired=False)
        scan["results"] = {" S01": [PACK], " S01E01": [EP1]}
        scan_watchlist()
        assert not any(q.endswith(" S01") for q in scan["queries"])
        assert scan["adds"] == [EP1["magnet"]]
        db.refresh(w)
        assert (w.season, w.episode) == (1, 2)

    def test_unknown_season_status_still_looks_for_pack(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True, tvmaze_id=None)
        scan["results"] = {" S01": [PACK]}
        scan_watchlist()
        assert scan["adds"] == [PACK["magnet"]]
        db.refresh(w)
        assert (w.season, w.episode) == (2, 1)
        # without TVMaze data we can only record the episode we were on
        assert [r.episode for r in db.query(WatchlistEpisode).filter_by(watchlist_id=w.id)] == [1]

    def test_pack_search_stops_after_repeated_failed_packs(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True)
        scan["tvmaze"][111] = _season_eps(1, 3)
        for i in range(3):
            db.add(Download(title=f"Show S01 pack {i}", torrent_hash=f"{i}" * 40,
                            status="failed", watchlist_id=w.id))
        db.commit()
        scan["results"] = {" S01": [PACK], " S01E01": [EP1]}
        scan_watchlist()
        assert scan["adds"] == [EP1["magnet"]]  # fell back to individual episodes

    def test_failed_pack_is_never_regrabbed(self, db, scan):
        w = _item(db, quality="1080p", codec="x265", prefer_season_packs=True)
        db.add(Download(title=PACK["title"], torrent_hash="9" * 40, status="failed", watchlist_id=w.id))
        db.commit()
        scan["tvmaze"][111] = _season_eps(1, 3)
        scan["results"] = {" S01": [PACK], " S01E01": [EP1]}
        scan_watchlist()
        assert scan["adds"] == [EP1["magnet"]]

    def test_profile_drives_search_and_pack_preference(self, db, scan):
        p = _profile(db, quality="1080p", codec="x265", max_size_mb=None, preferred_groups=None,
                     prefer_season_packs=True)
        w = _item(db, quality="480p", codec="x264", prefer_season_packs=False, profile_id=p.id)
        scan["tvmaze"][111] = _season_eps(1, 2)
        scan["results"] = {" S01": [PACK]}
        scan_watchlist()
        assert scan["adds"] == [PACK["magnet"]]          # pack preferred via the profile
        assert set(scan["qualities"]) == {"1080p"}      # profile quality, not the item's 480p
        db.refresh(w)
        assert w.season == 2

    def test_fallback_quality_used_by_scanner(self, db, scan):
        p = _profile(db, quality="4K", codec="x265", max_size_mb=None, preferred_groups=None,
                     prefer_season_packs=False, fallback_quality="1080p", fallback_after_hours=24)
        _item(db, profile_id=p.id)
        scan["tvmaze"][111] = [{"season": 1, "number": 1, "name": "",
                                "airs_at": datetime.now(timezone.utc) - timedelta(hours=60)}]

        def by_quality(query, quality=None, indexer="all", codec="x265", filter_adult=True):
            scan["qualities"].append(quality)
            return [dict(EP1)] if quality == "1080p" else []
        with patch("app.services.indexers.search_all", side_effect=by_quality):
            scan_watchlist()
        assert scan["adds"] == [EP1["magnet"]]
        assert scan["qualities"][-2:] == ["4K", "1080p"]


# ── retry queue: undoing a failed season pack ─────────────────────────────────


class TestFailedPackUndo:
    def test_failed_pack_rolls_back_rows_and_pointer(self, db, Session):
        from app.services.download_events import process_download_events

        w = _item(db, season=2, episode=1)
        for n in (1, 2, 3):
            db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=n, torrent_hash="9" * 40))
        db.add(Download(title="Show S01 COMPLETE", torrent_hash="9" * 40, status="error",
                        watchlist_id=w.id, download_path="/dl", qbit_removed=False))
        db.commit()
        with patch("app.database.SessionLocal", Session), \
             patch("app.services.scheduler._sync_download_statuses"), \
             patch("app.services.qbittorrent.get_all_torrent_statuses", return_value={}), \
             patch("app.services.qbittorrent.remove_torrent"), \
             patch("app.services.apprise_notify.notify") as notify:
            process_download_events()
        db.expire_all()
        assert db.query(WatchlistEpisode).count() == 0
        db.refresh(w)
        assert (w.season, w.episode) == (1, 1)
        assert "pack failed" in notify.call_args.kwargs["body"]

    def test_pointer_not_touched_if_user_moved_on(self, db, Session):
        from app.services.download_events import process_download_events

        w = _item(db, season=3, episode=4)
        db.add(WatchlistEpisode(watchlist_id=w.id, season=1, episode=1, torrent_hash="9" * 40))
        db.add(Download(title="Show S01 COMPLETE", torrent_hash="9" * 40, status="error",
                        watchlist_id=w.id, qbit_removed=False))
        db.commit()
        with patch("app.database.SessionLocal", Session), \
             patch("app.services.scheduler._sync_download_statuses"), \
             patch("app.services.qbittorrent.get_all_torrent_statuses", return_value={}), \
             patch("app.services.qbittorrent.remove_torrent"), \
             patch("app.services.apprise_notify.notify"):
            process_download_events()
        db.refresh(w)
        assert (w.season, w.episode) == (3, 4)


# ── profiles API ──────────────────────────────────────────────────────────────


@pytest.fixture()
def api(db):
    from app.routers import profiles, watchlist
    app = FastAPI()
    app.include_router(profiles.router)
    app.include_router(watchlist.router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


class TestProfilesApi:
    def _create(self, api, **kw):
        body = {"name": "Movies 4K", "quality": "4K", "codec": "x265"}
        body.update(kw)
        return api.post("/profiles", json=body)

    def test_create_and_list_with_counts(self, api, db):
        r = self._create(api, fallback_quality="1080p", fallback_after_hours=12, max_size_mb=8000)
        assert r.status_code == 201
        pid = r.json()["id"]
        _item(db, profile_id=pid)
        got = api.get("/profiles").json()
        assert got[0]["name"] == "Movies 4K" and got[0]["watchlist_count"] == 1
        assert got[0]["fallback_quality"] == "1080p" and got[0]["max_size_mb"] == 8000

    def test_duplicate_name_is_case_insensitive_409(self, api):
        self._create(api)
        assert self._create(api, name="movies 4k").status_code == 409

    @pytest.mark.parametrize("bad", [
        {"quality": "8K"}, {"codec": "divx"}, {"name": ""}, {"max_size_mb": 0},
        {"fallback_after_hours": 0}, {"fallback_quality": "potato"},
    ])
    def test_validation(self, api, bad):
        assert self._create(api, **bad).status_code == 422

    def test_update_and_rename_conflict(self, api):
        a = self._create(api, name="A").json()["id"]
        self._create(api, name="B")
        assert api.put(f"/profiles/{a}", json={"quality": "720p"}).json()["quality"] == "720p"
        assert api.put(f"/profiles/{a}", json={"name": "b"}).status_code == 409
        assert api.put(f"/profiles/{a}", json={"name": "A"}).status_code == 200  # own name is fine
        assert api.put("/profiles/999", json={"quality": "720p"}).status_code == 404

    def test_delete_bakes_values_into_shows_and_clears_default(self, api, db):
        pid = self._create(api, max_size_mb=7000, preferred_groups="flux",
                           prefer_season_packs=True).json()["id"]
        w = _item(db, quality="480p", codec="x264", profile_id=pid)
        db.add(Setting(key="default_profile_id", value=str(pid)))
        db.commit()
        r = api.delete(f"/profiles/{pid}")
        assert r.json() == {"ok": True, "detached": 1}
        db.expire_all()
        db.refresh(w)
        assert (w.profile_id, w.quality, w.codec, w.max_size_mb, w.preferred_groups,
                w.prefer_season_packs) == (None, "4K", "x265", 7000, "flux", True)
        assert db.query(Setting).filter_by(key="default_profile_id").one().value == ""
        assert api.delete(f"/profiles/{pid}").status_code == 404

    def test_watchlist_rejects_unknown_profile(self, api):
        body = {"title": "S", "search_query": "S", "profile_id": 424242}
        assert api.post("/watchlist", json=body).status_code == 422

    def test_watchlist_accepts_and_clears_profile(self, api):
        pid = self._create(api).json()["id"]
        wid = api.post("/watchlist", json={"title": "S", "search_query": "S", "profile_id": pid}).json()["id"]
        assert api.put(f"/watchlist/{wid}", json={"profile_id": None}).json()["profile_id"] is None


# ── migrations ────────────────────────────────────────────────────────────────


class TestMigrations:
    def test_old_database_gains_new_columns_and_tables(self, tmp_path):
        from app.migrations import run_migrations

        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE watchlist (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                search_query TEXT NOT NULL, quality TEXT, season INTEGER, episode INTEGER);
            CREATE TABLE downloads (id INTEGER PRIMARY KEY, title TEXT NOT NULL, status TEXT);
            INSERT INTO downloads(title, status) VALUES ('done', 'completed'), ('run', 'downloading');
        """)
        con.commit()
        con.close()

        engine = create_engine(f"sqlite:///{path}")
        run_migrations(bind=engine)
        run_migrations(bind=engine)  # idempotent; must not re-backfill or raise

        insp = inspect(engine)
        assert "quality_profiles" in insp.get_table_names()
        assert {"profile_id", "prefer_season_packs"} <= {c["name"] for c in insp.get_columns("watchlist")}
        with engine.connect() as c:
            rows = dict(c.exec_driver_sql("select title, notified_complete from downloads").fetchall())
        assert rows == {"done": 1, "run": 0}
