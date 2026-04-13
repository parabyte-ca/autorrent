"""
Unit tests for backend/app/routers/health.py.

The route function is called directly with a mock DB session so the full
FastAPI app (and its APScheduler lifespan) is never started.
"""
from unittest.mock import MagicMock, patch

from app.routers.health import get_health


def _mock_db() -> MagicMock:
    return MagicMock()


class TestGetHealth:
    def test_healthy(self):
        result = get_health(db=_mock_db())
        assert result["status"] == "ok"
        assert result["db_ok"] is True
        assert isinstance(result["uptime_seconds"], int)
        assert result["uptime_seconds"] >= 0
        assert result["version"] == "dev"

    def test_degraded_when_db_raises(self):
        db = _mock_db()
        db.execute.side_effect = Exception("DB is down")
        result = get_health(db=db)
        assert result["status"] == "degraded"
        assert result["db_ok"] is False
        assert "uptime_seconds" in result
        assert "version" in result

    def test_version_from_env(self):
        with patch.dict("os.environ", {"APP_VERSION": "1.2.3"}):
            result = get_health(db=_mock_db())
        assert result["version"] == "1.2.3"

    def test_version_defaults_to_dev_when_unset(self):
        import os
        env = {k: v for k, v in os.environ.items() if k != "APP_VERSION"}
        with patch.dict("os.environ", env, clear=True):
            result = get_health(db=_mock_db())
        assert result["version"] == "dev"

    def test_uptime_is_non_negative_integer(self):
        result = get_health(db=_mock_db())
        assert isinstance(result["uptime_seconds"], int)
        assert result["uptime_seconds"] >= 0

    def test_status_field_ok_when_db_ok(self):
        result = get_health(db=_mock_db())
        assert result["status"] == "ok"

    def test_status_field_degraded_when_db_fails(self):
        db = _mock_db()
        db.execute.side_effect = RuntimeError("connection lost")
        result = get_health(db=db)
        assert result["status"] == "degraded"
