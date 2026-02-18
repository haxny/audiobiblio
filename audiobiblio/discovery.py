"""
discovery — Multi-source episode discovery for mujrozhlas.cz programs.

Four layers:
1. yt-dlp flat-playlist (primary) — fastest, most complete
2. AJAX pagination — GET /ajax/ajax_list/show?page=N&size=50
3. HTML scraping — existing mrz_discover_children() as fallback
4. RAPI — api.mujrozhlas.cz/shows/{uuid}/episodes (richest metadata)

Returns merged DiscoveredEpisode list with source attribution.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests
import structlog

from .mrz_inspector import probe_url, classify_probe, mrz_discover_children, _is_mrz, _clean
from .ratelimit import mrz_limiter

log = structlog.get_logger()

# Full browser UA required by mujrozhlas.cz (bare Mozilla/5.0 gets 403)
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Regex to extract episode links from AJAX HTML snippets
_AJAX_LINK_RE = re.compile(
    r'href="(/[a-z0-9\-]+/[a-z0-9\-]+(?:/[a-z0-9\-]+)?)"',
    re.IGNORECASE,
)
# Regex to extract UUID-style ext_ids from AJAX data attributes
_AJAX_UUID_RE = re.compile(r'data-entity="([0-9a-f\-]{36})"', re.IGNORECASE)
# Regex to extract episode title from AJAX snippets
_AJAX_TITLE_RE = re.compile(
    r'<(?:h[234]|span)[^>]*class="[^"]*b-episode__title[^"]*"[^>]*>([^<]+)<',
    re.IGNORECASE,
)
# Regex to extract duration from AJAX snippets
_AJAX_DURATION_RE = re.compile(
    r'<(?:span|time)[^>]*class="[^"]*b-episode__duration[^"]*"[^>]*>([^<]+)<',
    re.IGNORECASE,
)


def _is_rozhlas(url: str) -> bool:
    """Return True if URL is a rozhlas.cz domain (not mujrozhlas)."""
    try:
        netloc = urlparse(url).netloc.lower()
        return "rozhlas.cz" in netloc and "mujrozhlas" not in netloc
    except Exception:
        return False


def normalize_rozhlas_url(url: str) -> str:
    """Convert rozhlas.cz program URLs to mujrozhlas.cz equivalents.

    Example: plus.rozhlas.cz/hlasy-pameti-9391766 → www.mujrozhlas.cz/hlasy-pameti
    """
    p = urlparse(url.strip())
    if not p.netloc or "mujrozhlas" in p.netloc or "rozhlas.cz" not in p.netloc:
        return url
    slug = p.path.strip("/").split("/")[0] if p.path else ""
    # Strip trailing numeric ID (e.g. -9391766)
    slug = re.sub(r'-\d{5,}$', '', slug)
    if slug:
        return f"https://www.mujrozhlas.cz/{slug}"
    return url


@dataclass
class DiscoveredEpisode:
    """An episode discovered from any source."""
    url: str
    title: str
    ext_id: Optional[str] = None
    duration_s: Optional[int] = None
    description: Optional[str] = None
    published_at: Optional[str] = None  # ISO or YYYYMMDD
    series: Optional[str] = None
    author: Optional[str] = None
    uploader: Optional[str] = None
    is_series_episode: bool = False  # True if part of a named multi-part series
    sources: set[str] = field(default_factory=set)
    original: dict = field(default_factory=dict)


def _parse_duration_text(text: str) -> int | None:
    """Parse '12:34' or '1:23:45' to seconds."""
    text = text.strip()
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return None


def _norm_url_for_merge(u: str) -> str:
    """Normalize URL for merge matching — lowercase host, strip trailing slash."""
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/")
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return u.strip().rstrip("/")


def _extract_show_rid(url: str) -> str | None:
    """Extract the show slug from a mujrozhlas program URL for AJAX endpoint."""
    try:
        p = urlparse(url)
        parts = [s for s in p.path.strip("/").split("/") if s]
        if parts:
            return parts[0]
    except Exception:
        pass
    return None


# ── Layer 1: yt-dlp ──────────────────────────────────────────────────

def _discover_ytdlp(url: str) -> list[DiscoveredEpisode]:
    """Use yt-dlp flat-playlist to discover episodes."""
    try:
        data = probe_url(url)
    except Exception as e:
        log.error("ytdlp_discovery_failed", url=url, error=str(e))
        return []

    pr = classify_probe(data, url)
    entries = pr.entries or []
    results = []
    for item in entries:
        orig = getattr(item, "original", {}) or {}
        ext_id = orig.get("id") or orig.get("display_id")
        duration = orig.get("duration")
        desc = _clean(orig.get("description"))
        upload_date = orig.get("upload_date")
        # Detect if episode is part of a named series (multi-part)
        is_series_ep = bool(orig.get("episode") or orig.get("season"))

        ep = DiscoveredEpisode(
            url=item.url,
            title=item.title or "",
            ext_id=ext_id,
            duration_s=int(duration) if duration else None,
            description=desc,
            published_at=upload_date,
            series=item.series,
            author=item.author,
            uploader=item.uploader or pr.uploader,
            is_series_episode=is_series_ep,
            sources={"ytdlp"},
            original=orig,
        )
        if ep.url:
            results.append(ep)

    log.info("ytdlp_discovery", url=url, count=len(results))
    return results


# ── Layer 2: AJAX pagination ─────────────────────────────────────────

def _discover_ajax(url: str) -> list[DiscoveredEpisode]:
    """Paginate the AJAX endpoint to discover episodes."""
    show_slug = _extract_show_rid(url)
    if not show_slug:
        log.warning("ajax_no_slug", url=url)
        return []

    base_p = urlparse(url)
    base_url = f"{base_p.scheme}://{base_p.netloc}"
    ajax_url = f"{base_url}/ajax/ajax_list/show"

    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/html, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": url,
    }

    results = []
    page = 0
    max_pages = 50  # safety limit

    while page < max_pages:
        mrz_limiter.wait()
        params = {"page": page, "size": 50, "show": show_slug}
        try:
            r = requests.get(ajax_url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
        except Exception as e:
            log.error("ajax_request_failed", page=page, error=str(e))
            break

        content = r.text
        if not content or len(content.strip()) < 10:
            break

        # Extract episode links from HTML snippets
        links = _AJAX_LINK_RE.findall(content)
        uuids = _AJAX_UUID_RE.findall(content)
        titles = _AJAX_TITLE_RE.findall(content)
        durations = _AJAX_DURATION_RE.findall(content)

        if not links:
            break

        for i, href in enumerate(links):
            abs_url = urljoin(base_url, href)
            # Skip links that are the program root itself
            parts = [s for s in urlparse(abs_url).path.strip("/").split("/") if s]
            if len(parts) < 2:
                continue

            title = _clean(titles[i]) if i < len(titles) else ""
            ext_id = uuids[i] if i < len(uuids) else None
            dur_text = durations[i] if i < len(durations) else None
            dur_s = _parse_duration_text(dur_text) if dur_text else None

            ep = DiscoveredEpisode(
                url=abs_url,
                title=title or "",
                ext_id=ext_id,
                duration_s=dur_s,
                sources={"ajax"},
            )
            results.append(ep)

        # Check if there's a next page — look for "more" link or next page indicator
        has_next = f"page={page + 1}" in content or "b-episode" in content
        if not has_next or len(links) < 10:
            break

        page += 1

    log.info("ajax_discovery", url=url, count=len(results), pages=page + 1)
    return results


# ── Layer 3: HTML scraping ────────────────────────────────────────────

def _discover_html(url: str) -> list[DiscoveredEpisode]:
    """Use existing HTML scraper as fallback."""
    try:
        children = mrz_discover_children(url)
    except Exception as e:
        log.error("html_discovery_failed", url=url, error=str(e))
        return []

    results = []
    for abs_url, title in children:
        ep = DiscoveredEpisode(
            url=abs_url,
            title=title,
            sources={"html"},
        )
        results.append(ep)

    log.info("html_discovery", url=url, count=len(results))
    return results


# ── Layer 4: RAPI ─────────────────────────────────────────────────────

def _discover_rapi(original_url: str) -> list[DiscoveredEpisode]:
    """Use RAPI to discover episodes from a rozhlas.cz show UUID."""
    from .rapi import extract_show_uuid, fetch_show_episodes

    uuid = extract_show_uuid(original_url)
    if not uuid:
        log.warning("rapi_no_uuid", url=original_url)
        return []

    episodes = fetch_show_episodes(uuid)
    log.info("rapi_discovery", url=original_url, uuid=uuid, count=len(episodes))
    return episodes


# ── Merge ─────────────────────────────────────────────────────────────

def _merge_discovered(
    ytdlp: list[DiscoveredEpisode],
    ajax: list[DiscoveredEpisode],
    html: list[DiscoveredEpisode],
    rapi: list[DiscoveredEpisode] | None = None,
) -> list[DiscoveredEpisode]:
    """
    Merge entries from all sources. Match by:
    1. ext_id (UUID) — exact match
    2. Normalized URL — strip trailing numeric suffixes, normalize host/scheme

    yt-dlp is primary; AJAX, HTML, and RAPI enrich metadata.
    """
    # Index by normalized URL and ext_id for fast lookup
    by_url: dict[str, DiscoveredEpisode] = {}
    by_ext_id: dict[str, DiscoveredEpisode] = {}

    def _add(ep: DiscoveredEpisode):
        norm = _norm_url_for_merge(ep.url)

        # Try to merge by ext_id first
        if ep.ext_id and ep.ext_id in by_ext_id:
            existing = by_ext_id[ep.ext_id]
            _enrich(existing, ep)
            return

        # Then by normalized URL
        if norm in by_url:
            existing = by_url[norm]
            _enrich(existing, ep)
            return

        # New entry
        by_url[norm] = ep
        if ep.ext_id:
            by_ext_id[ep.ext_id] = ep

    def _enrich(target: DiscoveredEpisode, source: DiscoveredEpisode):
        """Merge metadata from source into target."""
        target.sources |= source.sources
        if not target.title and source.title:
            target.title = source.title
        if not target.ext_id and source.ext_id:
            target.ext_id = source.ext_id
            by_ext_id[source.ext_id] = target
        if not target.duration_s and source.duration_s:
            target.duration_s = source.duration_s
        if not target.description and source.description:
            target.description = source.description
        if not target.published_at and source.published_at:
            target.published_at = source.published_at
        if not target.author and source.author:
            target.author = source.author
        if not target.uploader and source.uploader:
            target.uploader = source.uploader
        if not target.series and source.series:
            target.series = source.series
        if source.is_series_episode:
            target.is_series_episode = True

    # Add in priority order: yt-dlp first (primary), then AJAX, then HTML, then RAPI
    for ep in ytdlp:
        _add(ep)
    for ep in ajax:
        _add(ep)
    for ep in html:
        _add(ep)
    for ep in (rapi or []):
        _add(ep)

    # Return in yt-dlp order (preserving insertion order of by_url)
    return list(by_url.values())


# ── Public API ────────────────────────────────────────────────────────

def discover_program(
    url: str,
    *,
    skip_ajax: bool = False,
    skip_html: bool = False,
    skip_rapi: bool = False,
) -> list[DiscoveredEpisode]:
    """
    Multi-source discovery for a mujrozhlas.cz or rozhlas.cz program URL.

    Returns merged list of DiscoveredEpisode with source attribution.
    yt-dlp is always primary; AJAX, HTML, and RAPI provide supplementary data.

    For rozhlas.cz URLs, the URL is normalized to mujrozhlas.cz for yt-dlp/AJAX/HTML,
    and the original rozhlas.cz URL is used to extract the RAPI show UUID.
    """
    original_url = url
    rapi_entries: list[DiscoveredEpisode] = []

    # Handle rozhlas.cz URLs: normalize for standard layers, use original for RAPI
    if _is_rozhlas(url):
        if not skip_rapi:
            rapi_entries = _discover_rapi(original_url)
        url = normalize_rozhlas_url(url)
        log.info("rozhlas_url_normalized", original=original_url, normalized=url)

    if not _is_mrz(url):
        log.warning("discovery_not_mrz", url=url)
        # Fall back to yt-dlp only + any RAPI results for non-mujrozhlas URLs
        ytdlp = _discover_ytdlp(url)
        if rapi_entries:
            return _merge_discovered(ytdlp, [], [], rapi=rapi_entries)
        return ytdlp

    ytdlp_entries = _discover_ytdlp(url)
    ajax_entries = _discover_ajax(url) if not skip_ajax else []
    html_entries = _discover_html(url) if not skip_html else []

    # RAPI for mujrozhlas URLs too (if original was mujrozhlas, try extracting UUID)
    if not skip_rapi and not rapi_entries and _is_mrz(original_url):
        # For mujrozhlas URLs, we can still try RAPI if the page embeds a show UUID
        rapi_entries = _discover_rapi(original_url)

    merged = _merge_discovered(ytdlp_entries, ajax_entries, html_entries, rapi=rapi_entries)

    log.info(
        "discovery_complete",
        url=url,
        ytdlp=len(ytdlp_entries),
        ajax=len(ajax_entries),
        html=len(html_entries),
        rapi=len(rapi_entries),
        merged=len(merged),
    )
    return merged
