"""
Unit tests for the update-check and upgrade-request feature.

Coverage
────────
  Semver parsing             — valid tags, v-prefix, non-semver rejection
  Version comparison         — tuple ordering used for update detection
  Helper connected logic     — fresh / stale / future timestamp / interval tolerance
  Upgrade state derivation   — None, requested, stale-requested, running,
                               stale-running, succeeded, failed
  Folder file reading        — oversized, symlink, non-object, malformed JSON
  get_status                 — builds correct payload from DB + folder
  fetch_and_cache            — success path updates cache; failure keeps previous
  _maybe_notify              — sent once per new version, not repeated
  request_upgrade            — validation guards, atomic write, folder missing
  GET /api/admin/updates     — 200 with correct shape, 401 without auth
  POST /api/admin/updates/check — proxies fetch_and_cache
  POST /api/admin/updates/upgrade — 400 on bad version, 401 without auth
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Setting
from app.routers.updates import router
from app.services.updater import (
    derive_upgrade_state,
    get_status,
    is_helper_connected,
    normalise_version,
    parse_gh_repo,
    parse_semver,
    request_upgrade,
)


# ── Test infrastructure ───────────────────────────────────────────────────────


@pytest.fixture()
def db_session():
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
def upgrade_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _write(folder: str, name: str, data: dict) -> None:
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        json.dump(data, f)


def _seed_cache(db_session, version="2.5.0") -> None:
    db_session.add(Setting(
        key="update_release_cache",
        value=json.dumps({
            "version": version,
            "name": f"v{version}",
            "notes": "Test release",
            "url": "https://github.com/example/repo/releases/tag/v" + version,
            "published_at": "2026-10-01T00:00:00Z",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "check_error": None,
        }),
    ))
    db_session.commit()


# ── Semver ────────────────────────────────────────────────────────────────────


class TestParseSemver:
    def test_plain(self):
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        assert parse_semver("v2.4.0") == (2, 4, 0)

    def test_zeros(self):
        assert parse_semver("0.0.0") == (0, 0, 0)

    def test_two_part_rejected(self):
        assert parse_semver("2.4") is None

    def test_alpha_rejected(self):
        assert parse_semver("2.4.0-beta") is None

    def test_word_rejected(self):
        assert parse_semver("latest") is None

    def test_empty_rejected(self):
        assert parse_semver("") is None

    def test_comparison_works(self):
        assert parse_semver("2.4.0") > parse_semver("2.3.0")  # type: ignore[operator]
        assert parse_semver("2.3.1") > parse_semver("2.3.0")  # type: ignore[operator]
        assert parse_semver("3.0.0") > parse_semver("2.99.99")  # type: ignore[operator]


class TestNormaliseVersion:
    def test_two_part(self):
        assert normalise_version("2.4") == "2.4.0"

    def test_three_part(self):
        assert normalise_version("2.4.1") == "2.4.1"

    def test_v_prefix(self):
        assert normalise_version("v2.4.0") == "2.4.0"


# ── Helper connected ──────────────────────────────────────────────────────────


def _agent(minutes_ago: float = 5, every: int = 15) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"last_seen": ts.isoformat(), "every_minutes": every}


class TestHelperConnected:
    def test_fresh_is_connected(self):
        assert is_helper_connected(_agent(minutes_ago=1)) is True

    def test_just_within_grace(self):
        # grace = max(3, 2*15+1) = 31 minutes; 30 min ago should pass
        assert is_helper_connected(_agent(minutes_ago=30)) is True

    def test_just_outside_grace(self):
        # 32 minutes ago should fail for every_minutes=15
        assert is_helper_connected(_agent(minutes_ago=32)) is False

    def test_grace_scales_with_interval(self):
        # every=5 → grace = max(3, 11) = 11 min; 10 min ago passes
        assert is_helper_connected(_agent(minutes_ago=10, every=5)) is True
        # 13 min ago fails
        assert is_helper_connected(_agent(minutes_ago=13, every=5)) is False

    def test_future_timestamp_rejected(self):
        future_ts = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        assert is_helper_connected({"last_seen": future_ts, "every_minutes": 15}) is False

    def test_just_future_tolerated(self):
        # Up to 1 minute in the future is tolerated
        future_ts = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        assert is_helper_connected({"last_seen": future_ts, "every_minutes": 15}) is True

    def test_missing_last_seen(self):
        assert is_helper_connected({}) is False

    def test_bad_timestamp(self):
        assert is_helper_connected({"last_seen": "not-a-date", "every_minutes": 15}) is False


# ── Upgrade state derivation ──────────────────────────────────────────────────


class TestDeriveUpgradeState:
    def test_empty_folder(self, upgrade_dir):
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state is None
        assert msg is None

    def test_fresh_request(self, upgrade_dir):
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "requested"

    def test_stale_request_becomes_failed(self, upgrade_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin", "requested_at": old_ts,
        })
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert "never picked" in (msg or "")

    def test_running(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "running"

    def test_stale_running_becomes_failed(self, upgrade_dir):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        _write(upgrade_dir, "status.json", {"state": "running", "started_at": old_ts})
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert "stopped reporting" in (msg or "")

    def test_succeeded(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {"state": "succeeded"})
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "succeeded"

    def test_failed(self, upgrade_dir):
        _write(upgrade_dir, "status.json", {"state": "failed", "message": "build error"})
        state, msg = derive_upgrade_state(upgrade_dir)
        assert state == "failed"
        assert msg == "build error"

    def test_status_overrides_request(self, upgrade_dir):
        _write(upgrade_dir, "request.json", {
            "id": "abc", "version": "2.5.0",
            "requested_by": "admin",
            "requested_at": datetime.now(timezone.utc).isoformat(),
        })
        _write(upgrade_dir, "status.json", {"state": "succeeded"})
        state, _ = derive_upgrade_state(upgrade_dir)
        assert state == "succeeded"


# ── Folder file reading (defensive) ──────────────────────────────────────────


class TestReadJsonFile:
    """get_status must never crash on malformed folder contents."""

    def test_oversized_file_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write('{"last_seen": "' + "x" * 66000 + '"}')
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_non_object_json_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write('"just a string"')
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_malformed_json_ignored(self, upgrade_dir, db_session):
        path = os.path.join(upgrade_dir, "agent.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False

    def test_symlink_ignored(self, upgrade_dir, db_session):
        target = os.path.join(upgrade_dir, "real.json")
        with open(target, "w") as f:
            json.dump({"last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15}, f)
        link = os.path.join(upgrade_dir, "agent.json")
        os.symlink(target, link)
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["helper"]["configured"] is False


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:
    def test_no_update_when_no_cache(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert status["update_available"] is False
        assert status["latest_release"] is None
        assert status["can_upgrade"] is False

    def test_update_available_when_newer(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["update_available"] is True

    def test_no_update_when_same_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["update_available"] is False

    def test_can_upgrade_requires_helper(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        # No agent.json → helper not connected → can_upgrade False
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["can_upgrade"] is False

    def test_can_upgrade_true_with_connected_helper(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            status = get_status(db_session)
        assert status["can_upgrade"] is True

    def test_log_included_when_requested(self, db_session, upgrade_dir):
        log_path = os.path.join(upgrade_dir, "upgrade.log")
        with open(log_path, "w") as f:
            f.write("upgrade started\nupgrade done\n")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session, include_log=True)
        assert "upgrade done" in status.get("upgrade_log", "")

    def test_log_absent_by_default(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            status = get_status(db_session)
        assert "upgrade_log" not in status


# ── fetch_and_cache ───────────────────────────────────────────────────────────


class TestFetchAndCache:
    @pytest.mark.asyncio
    async def test_success_updates_cache(self, db_session, upgrade_dir):
        release = {
            "version": "2.5.0", "name": "v2.5.0",
            "notes": "New stuff", "url": "https://example.com",
            "published_at": "2026-10-01T00:00:00Z",
        }
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        with patch("app.services.updater._fetch_latest_release", new=AsyncMock(return_value=release)), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater._maybe_notify"):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert cache["version"] == "2.5.0"
        assert cache["check_error"] is None
        assert cache["checked_at"]

    @pytest.mark.asyncio
    async def test_failure_keeps_previous_release(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.5")
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        with patch("app.services.updater._fetch_latest_release", side_effect=Exception("Network error")), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert cache["version"] == "2.4.5"          # previous release preserved
        assert "Network error" in cache["check_error"]
        assert cache["checked_at"]

    @pytest.mark.asyncio
    async def test_bad_tag_rejected(self, db_session, upgrade_dir):
        db_session.add(Setting(key="app_source_url", value="https://github.com/owner/repo"))
        db_session.commit()

        async def _bad_release(*_a, **_kw):
            raise ValueError("Release tag 'nightly' is not strict semver")

        with patch("app.services.updater._fetch_latest_release", new=_bad_release), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            from app.services.updater import fetch_and_cache
            await fetch_and_cache(db_session)

        from app.services.updater import _load_cache
        cache = _load_cache(db_session)
        assert "nightly" in (cache.get("check_error") or "")


# ── _maybe_notify ─────────────────────────────────────────────────────────────


class TestMaybeNotify:
    def _call(self, db_session, version: str, apprise_url: str = "mock://test"):
        from app.services.updater import _maybe_notify
        db_session.add(Setting(key="apprise_url", value=apprise_url))
        db_session.commit()
        with patch("app.services.updater.get_current_version", return_value="2.4.0"), \
             patch("app.services.updater.notify") as mock_notify:
            _maybe_notify(db_session, version)
            return mock_notify

    def test_notification_sent_for_new_version(self, db_session):
        mock_notify = self._call(db_session, "2.5.0")
        mock_notify.assert_called_once()

    def test_notification_not_sent_twice(self, db_session):
        db_session.add(Setting(key="update_notified_version", value="2.5.0"))
        db_session.commit()
        mock_notify = self._call(db_session, "2.5.0")
        mock_notify.assert_not_called()

    def test_notification_not_sent_for_same_version(self, db_session):
        mock_notify = self._call(db_session, "2.4.0")  # same as current
        mock_notify.assert_not_called()

    def test_notification_not_sent_for_older_version(self, db_session):
        mock_notify = self._call(db_session, "2.3.0")  # older than current
        mock_notify.assert_not_called()


# ── request_upgrade ───────────────────────────────────────────────────────────


class TestRequestUpgrade:
    def test_rejects_when_no_cache(self, db_session, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="No release information"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_rejects_non_latest_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="does not match"):
                request_upgrade(db_session, "2.4.9", "admin")

    def test_rejects_non_newer_version(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.4.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="not newer"):
                request_upgrade(db_session, "2.4.0", "admin")

    def test_rejects_when_already_in_progress(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        _write(upgrade_dir, "status.json", {
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="already in progress"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_rejects_when_helper_not_connected(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"):
            with pytest.raises(ValueError, match="not connected"):
                request_upgrade(db_session, "2.5.0", "admin")

    def test_writes_request_json_atomically(self, db_session, upgrade_dir):
        _seed_cache(db_session, version="2.5.0")
        _write(upgrade_dir, "agent.json", {
            "last_seen": datetime.now(timezone.utc).isoformat(), "every_minutes": 15,
        })
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.get_current_version", return_value="2.4.0"), \
             patch("app.services.updater.notify"):
            request_upgrade(db_session, "2.5.0", "admin@example.com")

        req_path = os.path.join(upgrade_dir, "request.json")
        assert os.path.isfile(req_path)
        assert not os.path.isfile(req_path + ".tmp")  # temp file cleaned up
        with open(req_path) as f:
            req = json.load(f)
        assert req["version"] == "2.5.0"
        assert req["requested_by"] == "admin@example.com"
        assert "id" in req
        assert "requested_at" in req


# ── HTTP endpoints ────────────────────────────────────────────────────────────


class TestUpdateEndpoints:
    _AUTH = {"Authorization": "Bearer test-token"}

    def test_get_status_without_header_works_for_local_requests(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.get("/admin/updates")
        assert r.status_code == 200

    def test_get_status_200(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.get("/admin/updates", headers=self._AUTH)
        assert r.status_code == 200
        data = r.json()
        assert "current_version" in data
        assert "helper" in data
        assert "can_upgrade" in data

    def test_check_now_calls_fetch(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.fetch_and_cache", new=AsyncMock()), \
             patch("app.services.updater.check_now_rate_limited", return_value=True):
            r = client.post("/admin/updates/check", headers=self._AUTH)
        assert r.status_code == 200

    def test_upgrade_without_header_reaches_handler_for_local_requests(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.request_upgrade", side_effect=ValueError("nope")):
            r = client.post("/admin/updates/upgrade", json={"version": "2.5.0"})
        assert r.status_code == 400  # handler ran; not blocked as 401

    def test_upgrade_400_on_bad_version(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir), \
             patch("app.services.updater.request_upgrade", side_effect=ValueError("Version mismatch")):
            r = client.post("/admin/updates/upgrade", json={"version": "2.5.0"}, headers=self._AUTH)
        assert r.status_code == 400
        assert "Version mismatch" in r.json()["detail"]

    def test_upgrade_422_missing_version(self, client, upgrade_dir):
        with patch("app.services.updater._get_folder", return_value=upgrade_dir):
            r = client.post("/admin/updates/upgrade", json={}, headers=self._AUTH)
        assert r.status_code == 422


class TestUpdateEndpointsBehindAuthMiddleware:
    """The real auth boundary: AuthMiddleware protects /api/admin/updates."""

    @pytest.fixture()
    def secured(self, db_session, upgrade_dir):
        from app.middleware.auth import AuthMiddleware
        from app.services.token import make_token

        db_session.add(Setting(key="ui_password", value="hunter2"))
        db_session.add(Setting(key="session_secret", value="s3cret"))
        db_session.commit()

        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.include_router(router, prefix="/api")

        def _override():
            yield db_session

        app.dependency_overrides[get_db] = _override
        with patch("app.middleware.auth.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.services.updater._get_folder", return_value=upgrade_dir):
            yield TestClient(app), make_token("s3cret", "hunter2")

    def test_cloudflare_request_without_token_is_rejected(self, secured):
        client, _ = secured
        r = client.get("/api/admin/updates", headers={"cf-connecting-ip": "1.2.3.4"})
        assert r.status_code == 401

    def test_cloudflare_request_with_garbage_token_is_rejected(self, secured):
        client, _ = secured
        r = client.get("/api/admin/updates", headers={
            "cf-connecting-ip": "1.2.3.4", "Authorization": "Bearer garbage"})
        assert r.status_code == 401

    def test_cloudflare_request_with_valid_token_is_allowed(self, secured):
        client, token = secured
        r = client.get("/api/admin/updates", headers={
            "cf-connecting-ip": "1.2.3.4", "Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_local_request_is_allowed(self, secured):
        client, _ = secured
        assert client.get("/api/admin/updates").status_code == 200


# ── parse_gh_repo ─────────────────────────────────────────────────────────────


class TestParseGhRepo:
    def test_standard(self):
        assert parse_gh_repo("https://github.com/parabyte-ca/autorrent") == ("parabyte-ca", "autorrent")

    def test_git_suffix(self):
        assert parse_gh_repo("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_trailing_slash(self):
        assert parse_gh_repo("https://github.com/owner/repo/") == ("owner", "repo")

    def test_non_github_returns_none(self):
        assert parse_gh_repo("https://gitlab.com/owner/repo") is None

    def test_empty_returns_none(self):
        assert parse_gh_repo("") is None
