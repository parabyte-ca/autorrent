"""
Unit tests for backend/app/services/media_servers.py.

Uses unittest.mock to patch httpx.AsyncClient so no real network calls are made.
asyncio.run() drives coroutines without requiring pytest-asyncio.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.media_servers import refresh_jellyfin, refresh_plex, trigger_media_refresh


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _mock_response(status_code: int = 200, text: str = "", is_success: bool = True) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.is_success = is_success
    r.text = text
    return r


def _patch_client(get_responses=None, post_responses=None, side_effect=None):
    """Return (patcher, mock_client) with pre-configured get/post responses."""
    mock_client = AsyncMock()
    if side_effect is not None:
        mock_client.get.side_effect = side_effect
        mock_client.post.side_effect = side_effect
    else:
        if get_responses is not None:
            if isinstance(get_responses, list):
                mock_client.get.side_effect = get_responses
            else:
                mock_client.get.return_value = get_responses
        if post_responses is not None:
            if isinstance(post_responses, list):
                mock_client.post.side_effect = post_responses
            else:
                mock_client.post.return_value = post_responses
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    patcher = patch("app.services.media_servers.httpx.AsyncClient", return_value=mock_client)
    return patcher, mock_client


_SECTIONS_XML = (
    '<MediaContainer>'
    '<Directory key="1" type="show" title="TV Shows"/>'
    '<Directory key="2" type="movie" title="Movies"/>'
    '</MediaContainer>'
)


# ── refresh_plex ──────────────────────────────────────────────────────────────

class TestRefreshPlex:
    def test_success_with_library_key(self):
        patcher, mock_client = _patch_client(get_responses=_mock_response(200))
        with patcher:
            result = _run(refresh_plex("http://plex:32400", "token123", "1"))
        assert result is True
        mock_client.get.assert_called_once_with(
            "http://plex:32400/library/sections/1/refresh",
            params={"X-Plex-Token": "token123"},
        )

    def test_http_error_with_library_key(self):
        patcher, _ = _patch_client(get_responses=_mock_response(401, is_success=False))
        with patcher:
            result = _run(refresh_plex("http://plex:32400", "badtoken", "1"))
        assert result is False

    def test_success_without_library_key_refreshes_all(self):
        responses = [
            _mock_response(200, text=_SECTIONS_XML),
            _mock_response(200),
            _mock_response(200),
        ]
        patcher, mock_client = _patch_client(get_responses=responses)
        with patcher:
            result = _run(refresh_plex("http://plex:32400", "token123", None))
        assert result is True
        # 1 sections list + 1 refresh per library (2 libraries)
        assert mock_client.get.call_count == 3

    def test_sections_fetch_fails_without_library_key(self):
        patcher, _ = _patch_client(get_responses=_mock_response(403, is_success=False))
        with patcher:
            result = _run(refresh_plex("http://plex:32400", "token", None))
        assert result is False

    def test_empty_sections_without_library_key(self):
        empty_xml = "<MediaContainer></MediaContainer>"
        patcher, mock_client = _patch_client(get_responses=_mock_response(200, text=empty_xml))
        with patcher:
            result = _run(refresh_plex("http://plex:32400", "token", None))
        assert result is True
        assert mock_client.get.call_count == 1  # only the sections list call

    def test_connection_error(self):
        patcher, mock_client = _patch_client(side_effect=Exception("connection refused"))
        with patcher:
            result = _run(refresh_plex("http://unreachable:32400", "token", "1"))
        assert result is False


# ── refresh_jellyfin ──────────────────────────────────────────────────────────

class TestRefreshJellyfin:
    def test_success_with_library_id(self):
        patcher, mock_client = _patch_client(post_responses=_mock_response(200))
        with patcher:
            result = _run(refresh_jellyfin("http://jf:8096", "apikey", "abc123"))
        assert result is True
        mock_client.post.assert_called_once_with(
            "http://jf:8096/Items/abc123/Refresh",
            params={"api_key": "apikey"},
        )

    def test_success_without_library_id(self):
        patcher, mock_client = _patch_client(post_responses=_mock_response(200))
        with patcher:
            result = _run(refresh_jellyfin("http://jf:8096", "apikey", None))
        assert result is True
        mock_client.post.assert_called_once_with(
            "http://jf:8096/Library/Refresh",
            params={"api_key": "apikey"},
        )

    def test_success_with_204_no_content(self):
        # 204 must be treated as success even when is_success might be ambiguous
        resp = _mock_response(204, is_success=False)
        resp.status_code = 204
        patcher, _ = _patch_client(post_responses=resp)
        with patcher:
            result = _run(refresh_jellyfin("http://jf:8096", "apikey", None))
        assert result is True

    def test_http_error(self):
        patcher, _ = _patch_client(post_responses=_mock_response(401, is_success=False))
        with patcher:
            result = _run(refresh_jellyfin("http://jf:8096", "badkey", "abc123"))
        assert result is False

    def test_connection_error(self):
        patcher, _ = _patch_client(side_effect=Exception("connection refused"))
        with patcher:
            result = _run(refresh_jellyfin("http://unreachable:8096", "apikey", None))
        assert result is False


# ── trigger_media_refresh ─────────────────────────────────────────────────────

class TestTriggerMediaRefresh:
    def test_does_nothing_when_both_disabled(self):
        settings = {"plex_enabled": "false", "jellyfin_enabled": "false"}
        # Should complete without error and make no HTTP calls
        _run(trigger_media_refresh(settings))

    def test_fires_plex_when_enabled(self):
        settings = {
            "plex_enabled": "true",
            "plex_url": "http://plex:32400",
            "plex_token": "tok",
            "plex_library_key": "1",
            "jellyfin_enabled": "false",
        }
        patcher, mock_client = _patch_client(get_responses=_mock_response(200))
        with patcher:
            _run(trigger_media_refresh(settings))
        mock_client.get.assert_called_once()

    def test_fires_jellyfin_when_enabled(self):
        settings = {
            "plex_enabled": "false",
            "jellyfin_enabled": "true",
            "jellyfin_url": "http://jf:8096",
            "jellyfin_api_key": "key",
            "jellyfin_library_id": "",
        }
        patcher, mock_client = _patch_client(post_responses=_mock_response(200))
        with patcher:
            _run(trigger_media_refresh(settings))
        mock_client.post.assert_called_once()

    def test_skips_plex_when_url_missing(self):
        settings = {
            "plex_enabled": "true",
            "plex_url": "",
            "plex_token": "tok",
        }
        # No URL — should not make any calls
        patcher, mock_client = _patch_client(get_responses=_mock_response(200))
        with patcher:
            _run(trigger_media_refresh(settings))
        mock_client.get.assert_not_called()

    def test_never_raises_on_exception(self):
        settings = {
            "plex_enabled": "true",
            "plex_url": "http://plex:32400",
            "plex_token": "tok",
        }
        patcher, _ = _patch_client(side_effect=Exception("boom"))
        with patcher:
            # Must not raise
            _run(trigger_media_refresh(settings))
