import logging

from .jackett import search_jackett
from .nyaa import search_nyaa
from .tpb import search_tpb
from ...database import SessionLocal
from ...models import Setting

logger = logging.getLogger(__name__)


def search_all(query: str, quality: str | None = None, indexer: str = "all") -> list[dict]:
    db = SessionLocal()
    try:
        settings = {s.key: s.value for s in db.query(Setting).all()}
    finally:
        db.close()

    results: list[dict] = []

    if indexer in ("nyaa", "all"):
        try:
            results.extend(search_nyaa(query))
        except Exception as e:
            logger.warning("NYAA search failed: %s", e)

    if indexer in ("tpb", "all"):
        try:
            results.extend(search_tpb(query))
        except Exception as e:
            logger.warning("TPB search failed: %s", e)

    jackett_url = settings.get("jackett_url", "").strip()
    jackett_key = settings.get("jackett_api_key", "").strip()
    if indexer in ("jackett", "all") and jackett_url and jackett_key:
        try:
            results.extend(search_jackett(query, jackett_url, jackett_key))
        except Exception as e:
            logger.warning("Jackett search failed: %s", e)

    # Quality filter
    if quality and quality != "Any":
        q_lower = quality.lower()
        tag = "2160p" if quality == "4K" else q_lower
        results = [r for r in results if tag in r["title"].lower()]

    # Deduplicate by info_hash, fall back to title
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        key = r.get("info_hash") or r["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return sorted(unique, key=lambda r: r["seeds"], reverse=True)
