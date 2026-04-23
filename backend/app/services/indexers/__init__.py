import logging

from .eztv import search_eztv
from .jackett import search_jackett
from .leet337x import search_1337x
from .nyaa import search_nyaa
from .tpb import search_tpb
from .torrentgalaxy import search_torrentgalaxy
from .yts import search_yts
from ...database import SessionLocal
from ...models import Setting

logger = logging.getLogger(__name__)

# TPB category IDs 500–599 are all XXX content
_TPB_ADULT_CATEGORY_MIN = 500
_TPB_ADULT_CATEGORY_MAX = 599

# Keywords that reliably indicate adult/pornographic content.
# Chosen to be unambiguous — minimal risk of false positives.
_ADULT_KEYWORDS = frozenset({
    "xxx",
    "porn",
    "pornhub",
    "xvideos",
    "xhamster",
    "brazzers",
    "blacked",
    "bangbros",
    "realitykings",
    "mofos",
    "nubiles",
    "wankz",
    "teamskeet",
    "naughtyamerica",
    "digitalplayground",
    "wickedpictures",
    "hentai",
    "milf ",       # trailing space avoids matching "milford" etc.
    "creampie",
    "blowjob",
    "cumshot",
    "handjob",
    "gangbang",
    "fansly",
    "onlyfans",
    "javhd",
    "jav uncensored",
    "uncensored jav",
    "av idol",
    "adult film",
    "sex tape",
    "erotic film",
})


def _is_adult(result: dict) -> bool:
    """Return True if the result appears to be adult/pornographic content."""
    title_lower = result["title"].lower()

    # Check TPB category (500–599 = XXX)
    tpb_cat = result.get("_tpb_category")
    if tpb_cat is not None and _TPB_ADULT_CATEGORY_MIN <= tpb_cat <= _TPB_ADULT_CATEGORY_MAX:
        return True

    # Keyword scan on title
    for kw in _ADULT_KEYWORDS:
        if kw in title_lower:
            return True

    return False


def search_all(
    query: str,
    quality: str | None = None,
    indexer: str = "all",
    codec: str = "x265",
    filter_adult: bool = True,
) -> list[dict]:
    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
    finally:
        db.close()

    # Append codec to query when a specific codec is requested
    search_query = query
    if codec and codec.lower() != "any":
        search_query = f"{query} {codec}"

    results: list[dict] = []

    if indexer in ("nyaa", "all"):
        try:
            results.extend(search_nyaa(search_query))
        except Exception as e:
            logger.warning("NYAA search failed: %s", e)

    if indexer in ("tpb", "all"):
        try:
            results.extend(search_tpb(search_query))
        except Exception as e:
            logger.warning("TPB search failed: %s", e)

    if indexer in ("eztv", "all"):
        try:
            results.extend(search_eztv(search_query))
        except Exception as e:
            logger.warning("EZTV search failed: %s", e)

    if indexer in ("yts", "all"):
        try:
            results.extend(search_yts(search_query))
        except Exception as e:
            logger.warning("YTS search failed: %s", e)

    if indexer in ("tgx", "all"):
        try:
            results.extend(search_torrentgalaxy(search_query))
        except Exception as e:
            logger.warning("TorrentGalaxy search failed: %s", e)

    if indexer in ("1337x", "all"):
        try:
            results.extend(search_1337x(search_query))
        except Exception as e:
            logger.warning("1337x search failed: %s", e)

    jackett_url = settings.get("jackett_url", "").strip()
    jackett_key = settings.get("jackett_api_key", "").strip()
    if indexer in ("jackett", "all") and jackett_url and jackett_key:
        try:
            results.extend(search_jackett(search_query, jackett_url, jackett_key))
        except Exception as e:
            logger.warning("Jackett search failed: %s", e)

    # Adult content filter
    if filter_adult:
        before = len(results)
        results = [r for r in results if not _is_adult(r)]
        filtered = before - len(results)
        if filtered:
            logger.debug("Adult filter removed %d result(s)", filtered)

    # Quality filter
    if quality and quality != "Any":
        tag = "2160p" if quality == "4K" else quality.lower()
        results = [r for r in results if tag in r["title"].lower()]

    # Deduplicate by info_hash, fall back to title
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        key = r.get("info_hash") or r["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Strip internal-only fields before returning
    for r in unique:
        r.pop("_tpb_category", None)

    return sorted(unique, key=lambda r: r["seeds"], reverse=True)
