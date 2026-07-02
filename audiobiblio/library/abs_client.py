"""
abs_client — Audiobookshelf API client for library scan triggers.

Configure via env vars:
  ABS_URL      — Base URL (e.g. http://abs.local:13378)
  ABS_API_KEY  — API token from ABS settings
"""
from __future__ import annotations
import os
import structlog
import requests

log = structlog.get_logger()


def _abs_url() -> str | None:
    return os.environ.get("ABS_URL")


def _abs_headers() -> dict:
    key = os.environ.get("ABS_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def trigger_library_scan(library_id: str | None = None) -> bool:
    """
    Trigger a library scan on Audiobookshelf.
    If library_id is None, scans the first library found.
    Returns True on success.
    """
    base = _abs_url()
    if not base:
        log.warning("abs_not_configured", hint="Set ABS_URL and ABS_API_KEY env vars")
        return False

    headers = _abs_headers()
    base = base.rstrip("/")

    try:
        if not library_id:
            r = requests.get(f"{base}/api/libraries", headers=headers, timeout=10)
            r.raise_for_status()
            libs = r.json().get("libraries", [])
            if not libs:
                log.warning("abs_no_libraries")
                return False
            library_id = libs[0]["id"]

        r = requests.post(
            f"{base}/api/libraries/{library_id}/scan",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        log.info("abs_scan_triggered", library_id=library_id)
        return True

    except requests.RequestException as e:
        log.error("abs_scan_failed", error=str(e))
        return False


def get_library_items(library_id: str) -> list[dict]:
    """Get items from a library."""
    base = _abs_url()
    if not base:
        return []

    headers = _abs_headers()
    base = base.rstrip("/")

    try:
        r = requests.get(
            f"{base}/api/libraries/{library_id}/items",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except requests.RequestException as e:
        log.error("abs_list_failed", error=str(e))
        return []
