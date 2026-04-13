import asyncio
import logging
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


async def refresh_plex(url: str, token: str, library_key: str | None) -> bool:
    """Trigger a Plex library refresh. Returns True on success, False on any error."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if library_key:
                resp = await client.get(
                    f"{url}/library/sections/{library_key}/refresh",
                    params={"X-Plex-Token": token},
                )
                if not resp.is_success:
                    logger.error("Plex refresh failed — HTTP %s", resp.status_code)
                    return False
                return True
            else:
                sections_resp = await client.get(
                    f"{url}/library/sections",
                    params={"X-Plex-Token": token},
                )
                if not sections_resp.is_success:
                    logger.error("Plex: failed to list sections — HTTP %s", sections_resp.status_code)
                    return False
                root = ET.fromstring(sections_resp.text)
                keys = [d.get("key") for d in root.findall(".//Directory") if d.get("key")]
                for key in keys:
                    await client.get(
                        f"{url}/library/sections/{key}/refresh",
                        params={"X-Plex-Token": token},
                    )
                return True
    except Exception as e:
        logger.error("Plex refresh error: %s", e)
        return False


async def refresh_jellyfin(url: str, api_key: str, library_id: str | None) -> bool:
    """Trigger a Jellyfin library refresh. Returns True on success, False on any error."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if library_id:
                resp = await client.post(
                    f"{url}/Items/{library_id}/Refresh",
                    params={"api_key": api_key},
                )
            else:
                resp = await client.post(
                    f"{url}/Library/Refresh",
                    params={"api_key": api_key},
                )
            return resp.is_success or resp.status_code == 204
    except Exception as e:
        logger.error("Jellyfin refresh error: %s", e)
        return False


async def trigger_media_refresh(settings: dict) -> None:
    """Concurrently refresh Plex and/or Jellyfin libraries. Never raises."""
    try:
        plex_enabled = settings.get("plex_enabled", "false").lower() == "true"
        plex_url = settings.get("plex_url", "")
        plex_token = settings.get("plex_token", "")
        plex_library_key = settings.get("plex_library_key") or None

        jellyfin_enabled = settings.get("jellyfin_enabled", "false").lower() == "true"
        jellyfin_url = settings.get("jellyfin_url", "")
        jellyfin_api_key = settings.get("jellyfin_api_key", "")
        jellyfin_library_id = settings.get("jellyfin_library_id") or None

        tasks = []
        if plex_enabled and plex_url and plex_token:
            tasks.append(refresh_plex(plex_url, plex_token, plex_library_key))
        if jellyfin_enabled and jellyfin_url and jellyfin_api_key:
            tasks.append(refresh_jellyfin(jellyfin_url, jellyfin_api_key, jellyfin_library_id))

        if tasks:
            results = await asyncio.gather(*tasks)
            logger.info("Media refresh triggered — results: %s", results)
    except Exception as e:
        logger.error("Unexpected error in trigger_media_refresh: %s", e)
