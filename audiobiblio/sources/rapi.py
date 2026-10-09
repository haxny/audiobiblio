"""
rapi — Client for the mujrozhlas.cz RAPI (api.mujrozhlas.cz).

Extracts show UUIDs from rozhlas.cz pages and fetches episode metadata
via the public JSON API, returning DiscoveredEpisode objects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import requests
import structlog

from audiobiblio.core.ratelimit import mrz_limiter

log = structlog.get_logger()

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Regex to find the RAPI show UUID embedded in rozhlas.cz pages (links may be
# absolute or relative: "/rapi/view/show/<uuid>")
_SHOW_UUID_RE = re.compile(
    r'rapi/view/show/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)
# rozhlas.cz node id at the end of a program URL: …/vyvar-9259685
_NODE_RE = re.compile(r"-(\d{6,8})/?$")

_RAPI_BASE = "https://api.mujrozhlas.cz"
_TIMEOUT_S = 30


def _get(url: str, params: dict | None = None, follow: bool = True):
    """One polite GET against rAPI (shared crawl budget). Raises on transport errors."""
    mrz_limiter.wait()
    return requests.get(url, params=params, timeout=_TIMEOUT_S,
                        allow_redirects=follow,
                        headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"})


def _uuid_from_node(node: str) -> str | None:
    """rozhlas node id → show UUID via /show-redirect (301 → …/rapi/view/show/<uuid>)."""
    try:
        r = _get(f"{_RAPI_BASE}/show-redirect/{node}", follow=False)
    except requests.RequestException as e:
        log.warning("rapi_show_redirect_failed", node=node, error=str(e))
        return None
    m = _SHOW_UUID_RE.search(r.headers.get("Location", "") or "")
    return m.group(1) if m else None


def _uuid_from_title(name: str) -> str | None:
    """Exact show-title lookup; only an unambiguous single hit is trusted."""
    try:
        r = _get(f"{_RAPI_BASE}/shows", params={"filter[title]": name, "page[limit]": 3})
        data = r.json().get("data", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError) as e:
        log.warning("rapi_show_title_lookup_failed", name=name, error=str(e))
        return None
    return data[0]["id"] if len(data) == 1 else None


# Resolved show UUIDs live for the process lifetime: under the 300 req/h
# politeness budget the daily crawl must not re-resolve 1,300 shows.
_UUID_CACHE: dict[tuple, str] = {}


def resolve_show_uuid(urls: list[str], name: str | None = None) -> str | None:
    """Resolve a program's rAPI show UUID without scraping its page.

    mujrozhlas.cz show pages are client-rendered (no UUID in the HTML), so
    the page regex missed 517 slug-only targets. Order: a rozhlas node id
    in any of the URLs (target or its pair) → /show-redirect; otherwise an
    exact, unambiguous title match on /shows. Hits are cached per process.
    """
    key = (tuple(u for u in urls if u), name)
    if key in _UUID_CACHE:
        return _UUID_CACHE[key]
    uuid = _resolve_show_uuid(urls, name)
    if uuid:
        _UUID_CACHE[key] = uuid
    return uuid


def _resolve_show_uuid(urls: list[str], name: str | None) -> str | None:
    for url in urls:
        m = _NODE_RE.search((url or "").split("?")[0])
        if m:
            uuid = _uuid_from_node(m.group(1))
            if uuid:
                return uuid
    if name:
        return _uuid_from_title(name)
    return None


def best_audio_url(links: list[dict]) -> str | None:
    """Highest-quality link: the portal 'download' mp3 (its bitrate label
    lies low), then HLS, then whatever exists."""
    if not links:
        return None
    for pick in (lambda l: l.get("linkType") == "download",
                 lambda l: l.get("variant") == "hls"):
        hit = next((l for l in links if pick(l) and l.get("url")), None)
        if hit:
            return hit["url"]
    return links[0].get("url")


@dataclass(frozen=True)
class EpisodeWalk:
    """Result of one newest-first walk over a show's episode pages."""
    items: list[dict]        # episodes the caller flagged as needing action
    next_offset: int         # where a later walk should continue
    exhausted: bool          # reached the end of the show's history


def fetch_recent_episodes(show_uuid: str, needs_action, page_size: int = 100,
                          max_pages: int = 10, start_offset: int = 0,
                          stop_when_idle: bool = True) -> EpisodeWalk:
    """Newest-first walk of a show's episodes. Show listings carry only
    live episodes (expired ones drop out — verified 2026-10-08).

    `needs_action(e)` decides what the caller wants. With stop_when_idle the
    walk ends at the first page that brings nothing actionable — a daily
    check of a known show costs ONE request. Backfill walks pass
    start_offset and stop_when_idle=False to continue deep history.
    """
    out: list[dict] = []
    offset = start_offset
    for _ in range(max_pages):
        params = {"page[limit]": page_size, "page[offset]": offset, "sort": "-since"}
        try:
            r = _get(f"{_RAPI_BASE}/shows/{show_uuid}/episodes", params=params)
            if r.status_code != 200:
                log.warning("rapi_episodes_http", uuid=show_uuid, status=r.status_code)
                return EpisodeWalk(out, offset, False)
            data = r.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            log.warning("rapi_episodes_failed", uuid=show_uuid, offset=offset, error=str(e))
            return EpisodeWalk(out, offset, False)
        fresh = [e for e in data if needs_action(e)]
        out.extend(fresh)
        offset += len(data)
        if len(data) < page_size:
            return EpisodeWalk(out, offset, True)
        if stop_when_idle and not fresh:
            return EpisodeWalk(out, offset, False)
    return EpisodeWalk(out, offset, False)


def extract_show_uuid(rozhlas_url: str) -> str | None:
    """
    Show UUID for a rozhlas.cz page: node id → /show-redirect first (one
    cheap request), the embedded-link regex on the page as fallback.
    """
    uuid = resolve_show_uuid([rozhlas_url])
    if uuid:
        return uuid
    headers = {"User-Agent": _BROWSER_UA}
    mrz_limiter.wait()
    try:
        r = requests.get(rozhlas_url, headers=headers, timeout=_TIMEOUT_S)
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
    from audiobiblio.sources.discovery import DiscoveredEpisode

    page_size = 100
    offset = 0
    results: list[DiscoveredEpisode] = []

    while offset < limit:
        params = {"page[limit]": page_size, "page[offset]": offset, "sort": "-since"}
        try:
            r = _get(f"{_RAPI_BASE}/shows/{show_uuid}/episodes", params=params)
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

            serial_title = (attrs.get("mirroredSerial") or {}).get("title")

            # rAPI exposes no page URL (the old /episode/<uuid> guess is a
            # 403) — the audio link is the downloadable identity. ext_id is
            # the legacy contentId, the same id yt-dlp reports, so the merge
            # pairs rAPI with yt-dlp entries instead of duplicating them.
            ep_url = best_audio_url(attrs.get("audioLinks") or [])
            if not ep_url:
                continue  # expired — nothing to ingest
            content_id = ((ep_data.get("meta") or {}).get("ga") or {}).get("contentId")

            ep = DiscoveredEpisode(
                url=ep_url,
                title=title,
                # no contentId → no shared id space with yt-dlp; key by URL
                ext_id=str(content_id) if content_id else None,
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
