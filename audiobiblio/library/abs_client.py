"""
abs_client — backward-compat thin delegates to :class:`audiobiblio.library.abs.AbsClient`.

Configure via env vars:
  ABS_URL      — Base URL (e.g. http://abs.local:13378)
  ABS_API_KEY  — API token from ABS settings

All new code should import from ``audiobiblio.library.abs`` directly.
These module-level functions remain for existing call sites.
"""
from __future__ import annotations

import structlog

from audiobiblio.library.abs import AbsClient

log = structlog.get_logger()


def _get_client() -> AbsClient | None:
    """Build a client from legacy env vars; return None if not configured."""
    client = AbsClient.from_config()  # reads ABS_URL / ABS_API_KEY from env
    if not client.base_url:
        log.warning("abs_not_configured", hint="Set ABS_URL and ABS_API_KEY env vars")
        return None
    return client


def trigger_library_scan(library_id: str | None = None) -> bool:
    """Trigger a library scan on Audiobookshelf.

    If library_id is None, scans the first library found.
    Returns True on success.
    """
    client = _get_client()
    if client is None:
        return False

    try:
        if not library_id:
            libs = client.get_libraries()
            if not libs:
                log.warning("abs_no_libraries")
                return False
            library_id = libs[0]["id"]

        return client.trigger_scan(library_id)

    except Exception as exc:
        log.error("abs_scan_failed", error=str(exc))
        return False


def get_library_items(library_id: str) -> list[dict]:
    """Get items from a library."""
    client = _get_client()
    if client is None:
        return []

    try:
        return client.get_library_items(library_id)
    except Exception as exc:
        log.error("abs_list_failed", error=str(exc))
        return []
