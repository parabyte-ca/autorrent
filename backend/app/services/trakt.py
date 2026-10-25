"""Minimal Trakt client: read a user's public watchlist of shows.

Trakt has no webhook for a personal watchlist, so AutoRrent polls it.  Public
watchlists need only an app *client ID* (no OAuth).
"""
import asyncio
import logging
import os
import re
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.trakt.tv"
_PAGE_SIZE = 100
_MAX_PAGES = 10

# fullmatch, not match+$: "$" also matches before a trailing newline, which must
# never reach an HTTP header or URL.
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CLIENT_ID = re.compile(r"[A-Za-z0-9]{16,128}")


class TraktError(Exception):
    """A Trakt problem with a short, user-facing message."""


def valid_username(name: str) -> bool:
    return bool(_USERNAME.fullmatch(name or ""))


def valid_client_id(client_id: str) -> bool:
    return bool(_CLIENT_ID.fullmatch(client_id or ""))


def _headers(client_id: str) -> dict[str, str]:
    return {
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
        "Content-Type": "application/json",
        "User-Agent": f"AutoRrent/{os.environ.get('APP_VERSION', 'dev')}",
    }


def _normalise(entry: dict) -> dict | None:
    show = entry.get("show")
    if entry.get("type", "show") != "show" or not isinstance(show, dict):
        return None
    ids = show.get("ids") or {}
    if not ids.get("trakt") or not show.get("title"):
        return None
    return {
        "trakt_id": int(ids["trakt"]),
        "title": show["title"],
        "year": show.get("year"),
        "imdb": ids.get("imdb"),
        "tvdb": ids.get("tvdb"),
        "listed_at": entry.get("listed_at") or "",
    }


async def fetch_watchlist_shows(client_id: str, username: str) -> list[dict]:
    """Every show on the user's watchlist, oldest-added first. Raises TraktError."""
    if not valid_client_id(client_id):
        raise TraktError("That doesn't look like a Trakt client ID.")
    if not valid_username(username):
        raise TraktError("That doesn't look like a Trakt username.")

    url = f"{_BASE}/users/{urllib.parse.quote(username, safe='')}/watchlist/shows"
    shows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            page = 1
            while page <= _MAX_PAGES:
                resp = None
                for attempt in range(2):
                    resp = await client.get(url, headers=_headers(client_id),
                                            params={"page": page, "limit": _PAGE_SIZE})
                    if resp.status_code == 429 and attempt == 0:
                        try:
                            delay = min(float(resp.headers.get("retry-after", "1")), 5.0)
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(delay)
                        continue
                    break
                if resp.status_code == 404:
                    raise TraktError(f"Trakt user '{username}' was not found.")
                if resp.status_code in (401, 403):
                    raise TraktError(
                        "Trakt refused the request. Check the client ID, and that the watchlist "
                        "is public (Trakt → Settings → Privacy → Watchlist: Anyone)."
                    )
                if not resp.is_success:
                    raise TraktError(f"Trakt returned an error (HTTP {resp.status_code}).")
                try:
                    data = resp.json()
                except ValueError:
                    raise TraktError("Trakt sent an unreadable response.")
                if not isinstance(data, list):
                    raise TraktError("Trakt sent an unexpected response.")
                shows.extend(n for n in map(_normalise, data) if n)
                try:
                    pages = int(resp.headers.get("x-pagination-page-count", "1"))
                except ValueError:
                    pages = 1
                if page >= pages or not data:
                    break
                page += 1
    except httpx.HTTPError as exc:
        logger.warning("Trakt request failed: %s", exc)
        raise TraktError("Could not reach Trakt. Check your connection and try again.")

    shows.sort(key=lambda s: s["listed_at"])
    return shows
