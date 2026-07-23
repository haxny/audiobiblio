"""pairing — dual-source doctrine: every rozhlas.cz program/book target has a
mujrozhlas.cz counterpart (and vice versa). Both must be crawled, their
media + json + html gathered, merged via ext_id, gaps filled and the better
variant chosen. This module derives the counterpart URL.

Bridge (verified live, docs/research/2026-07-23-api-mujrozhlas.md):
  station URL slug ends in the Drupal node id  →
  GET api.mujrozhlas.cz/show-redirect/{id} (or serial-redirect for books) →
  301 to  www.mujrozhlas.cz/rapi/view/show/{uuid}  →
  following redirects lands on the real mujrozhlas.cz page.
Fallback: fetch the station page and read the embedded rapi show link
(rapi.extract_show_uuid).
"""
from __future__ import annotations

import re

import requests
import structlog

from audiobiblio.sources.rozhlas_station import is_station_program_url

log = structlog.get_logger()

_API = "https://api.mujrozhlas.cz"
_UA = {"User-Agent": "Mozilla/5.0 (audiobiblio)"}
_NODE_ID_RE = re.compile(r"-(\d{6,})/?$")


def _resolve(url: str, timeout: int = 30) -> str | None:
    """Follow redirects; return the final URL or None."""
    try:
        r = requests.get(url, headers=_UA, timeout=timeout, allow_redirects=True)
        if r.status_code < 400 and r.url and r.url != url:
            return r.url
    except Exception as e:
        log.debug("pairing_resolve_failed", url=url, error=str(e))
    return None


def derive_mujrozhlas_counterpart(station_url: str) -> str | None:
    """Station program/book URL → its mujrozhlas.cz page URL, or None.

    Tries show-redirect then serial-redirect on the node id from the slug;
    falls back to reading the rapi show uuid out of the page HTML.
    """
    if not is_station_program_url(station_url):
        return None
    m = _NODE_ID_RE.search(station_url.split("?")[0])
    if m:
        node_id = m.group(1)
        for kind in ("show-redirect", "serial-redirect"):
            final = _resolve(f"{_API}/{kind}/{node_id}")
            if final and "mujrozhlas.cz" in final and "/rapi/" not in final:
                return final
    # Fallback: embedded rapi link on the page itself
    try:
        from audiobiblio.sources.rapi import extract_show_uuid
        uuid = extract_show_uuid(station_url)
        if uuid:
            final = _resolve(f"https://www.mujrozhlas.cz/rapi/view/show/{uuid}")
            if final and "/rapi/" not in final:
                return final
    except Exception:
        log.debug("pairing_fallback_failed", url=station_url, exc_info=True)
    return None


def ensure_pair(session, target) -> bool:
    """Fill target.paired_url when derivable. Returns True if newly set."""
    if target.paired_url or not is_station_program_url(target.url):
        return False
    pair = derive_mujrozhlas_counterpart(target.url)
    if pair and pair.rstrip("/") != target.url.rstrip("/"):
        target.paired_url = pair
        session.commit()
        log.info("pairing_set", target_id=target.id, url=target.url, paired=pair)
        return True
    return False
