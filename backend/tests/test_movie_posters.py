"""Movie posters from TMDB: only when a key is saved, only after TVMaze has no such
show, year-aware, and an outage is never remembered as "no poster"."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Setting, WatchlistItem
from app.routers import settings as settings_router
from app.routers import watchlist as watchlist_router
from app.services import show_metadata, tmdb, tvmaze

KEY = "a" * 32
TOKEN = "x" * 24 + "." + "y" * 24 + "." + "z" * 24


def movie(id_, title, date="2021-10-22", poster="/p.jpg", overview="A <b>big</b> film."):
    return {"id": id_, "title": title, "release_date": date, "poster_path": poster, "overview": overview}


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


def run(coro):
    return asyncio.run(coro)


# ── keys ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key,ok", [
    (KEY, True), (TOKEN, True), ("A1" * 16, True),
    ("", False), (None, False), ("short", False), (KEY + "\n", False),
    ("g" * 32, False), (KEY + "x", False),
])
def test_valid_key(key, ok):
    assert tmdb.valid_key(key) is ok


def test_v3_key_goes_in_the_query_and_v4_token_in_the_header():
    h, p = tmdb._auth(KEY)
    assert p == {"api_key": KEY} and "Authorization" not in h
    h, p = tmdb._auth(TOKEN)
    assert p == {} and h["Authorization"] == f"Bearer {TOKEN}"


# ── search ────────────────────────────────────────────────────────────────────


def _search(results, name="Dune", year=None):
    with patch.object(tmdb, "_get", AsyncMock(return_value={"results": results})):
        return run(tmdb.search_movie(KEY, name, year))


def test_search_skips_results_without_a_poster():
    m = _search([movie(1, "Dune", poster=None), movie(2, "Dune")])
    assert m["id"] == 2


def test_year_must_match_within_a_year():
    results = [movie(1, "Dune", "1984-12-14"), movie(2, "Dune", "2021-10-22")]
    assert _search(results, year=2021)["id"] == 2
    assert _search(results, year=1984)["id"] == 1
    assert _search(results, year=2000) is None


def test_no_year_takes_the_first_match():
    assert _search([movie(1, "Dune", "1984-12-14"), movie(2, "Dune")])["id"] == 1


def test_poster_url_is_https_tmdb_image():
    assert tmdb.poster_url(movie(1, "x", poster="/abc.jpg")) == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert tmdb.poster_url(movie(1, "x", poster=None)) is None
    assert tmdb.poster_url(movie(1, "x", poster="javascript:alert(1)")) is None


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_bad_responses_are_unavailable_not_not_found(status):
    transport = httpx.MockTransport(lambda req: httpx.Response(status, json={}))
    real = httpx.AsyncClient
    with patch("app.services.tmdb.httpx.AsyncClient", lambda **kw: real(transport=transport, **kw)):
        with pytest.raises(tmdb.TMDBUnavailable):
            run(tmdb.search_movie(KEY, "Dune"))


def test_network_error_is_unavailable():
    def boom(req):
        raise httpx.ConnectError("down")
    real = httpx.AsyncClient
    with patch("app.services.tmdb.httpx.AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom), **kw)):
        with pytest.raises(tmdb.TMDBUnavailable):
            run(tmdb.search_movie(KEY, "Dune"))


def test_check_key_messages():
    assert run(tmdb.check_key("nope"))[0] is False
    with patch.object(tmdb, "_get", AsyncMock(return_value={})):
        assert run(tmdb.check_key(KEY)) == (True, "Connected to TMDB.")
    with patch.object(tmdb, "_get", AsyncMock(side_effect=tmdb.TMDBUnavailable("TMDB rejected the API key."))):
        assert run(tmdb.check_key(KEY)) == (False, "TMDB rejected the API key.")


# ── enrich_item ───────────────────────────────────────────────────────────────


def _enrich(item, key=KEY, show=None, movie_result=None, tmdb_error=None, tvmaze_error=None):
    tm = AsyncMock(side_effect=tvmaze_error) if tvmaze_error else AsyncMock(return_value=show)
    mv = AsyncMock(side_effect=tmdb_error) if tmdb_error else AsyncMock(return_value=movie_result)
    with patch.object(show_metadata, "find_show", tm), patch.object(tmdb, "search_movie", mv):
        return run(show_metadata.enrich_item(item, key)), tm, mv


def test_movie_gets_poster_blurb_and_date_from_tmdb(db):
    item = add(db, "Dune 2021")
    result, _, mv = _enrich(item, movie_result=movie(7, "Dune"))
    assert result == show_metadata.FOUND
    assert item.poster_url == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert item.summary == "A big film."
    assert item.premiered == "2021-10-22"
    assert item.tvmaze_id is None and item.metadata_attempts == 0
    mv.assert_awaited_once_with(KEY, "Dune", 2021)


def test_tvmaze_wins_so_tmdb_is_not_asked_for_a_known_show(db):
    item = add(db, "Severance")
    s = {"id": 9, "name": "Severance", "image": {"medium": "https://static.tvmaze.com/9.jpg"}, "status": "Running"}
    result, _, mv = _enrich(item, show=s)
    assert result == show_metadata.FOUND and item.tvmaze_id == 9
    mv.assert_not_awaited()


def test_without_a_key_tmdb_is_never_asked(db):
    item = add(db, "Dune 2021")
    result, _, mv = _enrich(item, key=None)
    assert result == show_metadata.NOT_FOUND
    mv.assert_not_awaited()


def test_unknown_to_both_is_remembered_with_backoff(db):
    item = add(db, "Nothing Here")
    result, _, _ = _enrich(item)
    assert result == show_metadata.NOT_FOUND
    assert item.metadata_checked_at is not None and item.metadata_attempts == 1
    assert not show_metadata.metadata_due(item)


def test_tmdb_outage_is_not_remembered(db):
    item = add(db, "Dune 2021")
    result, _, _ = _enrich(item, tmdb_error=tmdb.TMDBUnavailable("HTTP 503"))
    assert result == show_metadata.UNAVAILABLE
    assert item.metadata_checked_at is None and item.metadata_attempts in (0, None)


def test_tvmaze_outage_does_not_fall_through_to_tmdb(db):
    item = add(db, "Dune 2021")
    result, _, mv = _enrich(item, tvmaze_error=tvmaze.TVMazeUnavailable("down"))
    assert result == show_metadata.UNAVAILABLE
    mv.assert_not_awaited()


def test_tmdb_key_helper_ignores_junk(db):
    assert show_metadata.tmdb_key(db) is None
    db.add(Setting(key="tmdb_api_key", value="junk"))
    db.commit()
    assert show_metadata.tmdb_key(db) is None
    db.query(Setting).delete()
    db.add(Setting(key="tmdb_api_key", value=f"  {KEY}  "))
    db.commit()
    assert show_metadata.tmdb_key(db) == KEY


# ── HTTP: settings + watchlist ────────────────────────────────────────────────


@pytest.fixture()
def client(Session):
    app = FastAPI()
    app.include_router(settings_router.router)
    app.include_router(watchlist_router.router)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_test_endpoint_reports_the_result(client):
    with patch.object(tmdb, "check_key", AsyncMock(return_value=(True, "Connected to TMDB."))):
        r = client.post("/settings/test-tmdb", json={"api_key": f" {KEY} "})
    assert r.json() == {"ok": True, "message": "Connected to TMDB."}


def test_saving_a_key_makes_posterless_movies_due_again(client, Session):
    s = Session()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    movie_item = add(s, "Dune 2021", metadata_checked_at=old, metadata_attempts=3)
    show_item = add(s, "Some Show", metadata_checked_at=old, metadata_attempts=2, tvmaze_id=5)
    done = add(s, "Has Poster", metadata_checked_at=old, poster_url="https://x/y.jpg", metadata_attempts=0)
    ids = (movie_item.id, show_item.id, done.id)
    s.close()

    client.put("/settings", json={"tmdb_api_key": KEY})

    s = Session()
    m, sh, d = (s.get(WatchlistItem, i) for i in ids)
    assert m.metadata_checked_at is None and m.metadata_attempts == 0
    assert sh.metadata_attempts == 2 and sh.metadata_checked_at is not None   # real shows untouched
    assert d.metadata_checked_at is not None
    s.close()


def test_saving_a_junk_key_resets_nothing(client, Session):
    s = Session()
    item = add(s, "Dune 2021", metadata_checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc), metadata_attempts=3)
    iid = item.id
    s.close()
    client.put("/settings", json={"tmdb_api_key": "junk"})
    s = Session()
    assert s.get(WatchlistItem, iid).metadata_attempts == 3
    s.close()


def test_adding_a_movie_uses_the_saved_key(client, Session):
    client.put("/settings", json={"tmdb_api_key": KEY})
    with patch.object(show_metadata, "find_show", AsyncMock(return_value=None)), \
         patch.object(tmdb, "search_movie", AsyncMock(return_value=movie(7, "Dune"))):
        r = client.post("/watchlist", json={"title": "Dune 2021", "search_query": "Dune 2021"})
    assert r.status_code == 201
    assert r.json()["poster_url"] == "https://image.tmdb.org/t/p/w342/p.jpg"


def test_adding_a_movie_without_a_key_still_works(client):
    with patch.object(show_metadata, "find_show", AsyncMock(return_value=None)), \
         patch.object(tmdb, "search_movie", AsyncMock()) as mv:
        r = client.post("/watchlist", json={"title": "Dune 2021", "search_query": "Dune 2021"})
    assert r.status_code == 201 and r.json()["poster_url"] is None
    mv.assert_not_awaited()
