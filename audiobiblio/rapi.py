"""
rapi — Client for the mujrozhlas.cz RAPI (api.mujrozhlas.cz).

Extracts show UUIDs from rozhlas.cz pages and fetches episode metadata
via the public JSON API, returning DiscoveredEpisode objects.
"""
from __future__ import annotations

import re
from datetime import datetime

import requests
import structlog

from .ratelimit import mrz_limiter

log = structlog.get_logger()

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Regex to find the RAPI show UUID embedded in rozhlas.cz pages
_SHOW_UUID_RE = re.compile(
    r'mujrozhlas\.cz/rapi/view/show/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)

_RAPI_BASE = "https://api.mujrozhlas.cz"


def extract_show_uuid(rozhlas_url: str) -> str | None:
    """
    Fetch a rozhlas.cz page and extract the show UUID from embedded RAPI links.

    Returns the UUID string or None if not found.
    """
    headers = {"User-Agent": _BROWSER_UA}
    mrz_limiter.wait()
    try:
        r = requests.get(rozhlas_url, headers=headers, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.error("rapi_extract_uuid_failed", url=rozhlas_url, error=str(e))
        return None

    m = _SHOW_UUID_RE.search(r.text)
    if m:
        uuid = m.group(1)
        log.info("rapi_show_uuid_extracted", url=rozhlas_url, uuid=uuid)
        return uuid

    log.warning("rapi_no_show_uuid", url=rozhlas_url)
    return None


def _strip_html(text: str | None) -> str | None:
    """Strip HTML tags from a string, returning plain text."""
    if not text:
        return None
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or None


def fetch_show_episodes(show_uuid: str, limit: int = 500) -> list:
    """
    Paginate the RAPI episodes endpoint for a show.

    GET /shows/{uuid}/episodes?page[limit]=50&page[offset]=0

    Returns a list of DiscoveredEpisode objects (imported lazily to avoid
    circular imports).
    """
    from .discovery import DiscoveredEpisode

    headers = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
    page_size = 50
    offset = 0
    results: list[DiscoveredEpisode] = []

    while offset < limit:
        mrz_limiter.wait()
        url = f"{_RAPI_BASE}/shows/{show_uuid}/episodes"
        params = {"page[limit]": page_size, "page[offset]": offset}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error("rapi_fetch_failed", uuid=show_uuid, offset=offset, error=str(e))
            break

        episodes = data.get("data", [])
        if not episodes:
            break

        for ep_data in episodes:
            attrs = ep_data.get("attributes", {})
            ep_uuid = ep_data.get("id")

            title = attrs.get("title", "")
            description = _strip_html(attrs.get("description"))
            duration_s = attrs.get("duration")  # seconds (int)

            # Parse publication date
            published_at = None
            since_str = attrs.get("since")
            if since_str:
                try:
                    # RAPI returns ISO 8601 like "2024-03-15T10:00:00+01:00"
                    published_at = since_str[:10]  # YYYY-MM-DD
                except Exception:
                    pass

            # Build mujrozhlas.cz episode URL from serial + episode UUID
            serial = attrs.get("serial", {})
            serial_title = serial.get("title") if isinstance(serial, dict) else None

            # The RAPI sometimes includes a mirroredShow or related link
            # Construct URL from the episode's own attributes
            ep_url = f"https://www.mujrozhlas.cz/episode/{ep_uuid}" if ep_uuid else ""

            ep = DiscoveredEpisode(
                url=ep_url,
                title=title,
                ext_id=ep_uuid,
                duration_s=int(duration_s) if duration_s else None,
                description=description,
                published_at=published_at,
                series=serial_title,
                sources={"rapi"},
            )
            results.append(ep)

        # Check if we got a full page (more may follow)
        if len(episodes) < page_size:
            break
        offset += page_size

    log.info("rapi_episodes_fetched", uuid=show_uuid, count=len(results))
    return results
