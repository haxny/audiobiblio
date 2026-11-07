"""URL normalization — the single home for URL comparison logic.

Moved from dedupe.py/crawler.py duplicates (see docs/decisions/).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Trailing numeric suffix pattern (re-air IDs like -2941669)
_REAIR_SUFFIX_RE = re.compile(r"-\d{7,}$")


def norm_url(u: str | None) -> str:
    """Basic URL normalization: lowercase host, strip trailing slash."""
    if not u:
        return ""
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return u.strip().rstrip("/")


def norm_url_strip_reair(u: str | None) -> str:
    """Normalize URL and strip trailing re-air numeric suffixes."""
    norm = norm_url(u)
    if not norm:
        return ""
    try:
        p = urlparse(norm)
        path = _REAIR_SUFFIX_RE.sub("", p.path)
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))
    except Exception:
        return norm


_MEDIA_URL_RE = re.compile(
    r"\.(mp3|m4a|m3u8|mpd|aac)(\?|/|$)"
    r"|//(croaod\.cz|portal\.rozhlas\.cz/sites/default/files/audios)/", re.I)


def is_media_url(url: str | None) -> bool:
    """Direct audio/stream link (portal mp3, croaod HLS) — not a web page."""
    return bool(url and _MEDIA_URL_RE.search(url))


def page_url(url: str | None, alias_urls: list[str | None]) -> str | None:
    """An episode's web page: *url* unless it is a bare audio/stream link
    (rAPI ingest, re-air revival) — then the first page-like alias."""
    if url and not is_media_url(url):
        return url
    return next((a for a in alias_urls if a and not is_media_url(a)), None)


def rapi_episode_page_url(uuid: str) -> str:
    """Episode-level mujrozhlas.cz page for an rAPI episode UUID. rAPI carries
    no web link; www.mujrozhlas.cz/rapi/view/{show,serial,episode}/{uuid}
    redirects to the real page (the same bridge show/serial-redirect use)."""
    return f"https://www.mujrozhlas.cz/rapi/view/episode/{uuid}"
