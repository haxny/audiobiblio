"""
discovery — Multi-source episode discovery for mujrozhlas.cz programs.

Three layers:
1. yt-dlp flat-playlist (primary) — fastest, most complete
2. HTML scraping — existing mrz_discover_children() as fallback
3. RAPI — api.mujrozhlas.cz/shows/{uuid}/episodes (richest metadata)

(The former AJAX layer — /ajax/ajax_list/show — answers 400/403 since
2026-10; it was removed rather than left burning the request budget.)

Returns merged DiscoveredEpisode list with source attribution.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import structlog

from audiobiblio.sources.mrz_inspector import probe_url, classify_probe, mrz_discover_children, _is_mrz, _clean

log = structlog.get_logger()


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


def _norm_url_for_merge(u: str) -> str:
    """Normalize URL for merge matching — lowercase host, strip trailing slash."""
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/")
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return u.strip().rstrip("/")


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

def _discover_rapi(original_url: str, show_name: str | None = None) -> list[DiscoveredEpisode]:
    """Use RAPI to discover episodes: show UUID via node id / exact title
    (mujrozhlas show pages carry no UUID), page regex as the last resort."""
    from audiobiblio.sources.rapi import (
        extract_show_uuid, fetch_show_episodes, resolve_show_uuid,
    )

    uuid = resolve_show_uuid([original_url], name=show_name)
    if not uuid and _is_rozhlas(original_url):
        uuid = extract_show_uuid(original_url)
    if not uuid:
        log.warning("rapi_no_uuid", url=original_url)
        return []

    episodes = fetch_show_episodes(uuid)
    log.info("rapi_discovery", url=original_url, uuid=uuid, count=len(episodes))
    return episodes


# ── Merge ─────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Slugify a title for URL-slug matching (strip diacritics, lowercase, dash-separated)."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


def _url_slug(url: str) -> str:
    """Extract the last path segment of a URL as a slug for matching."""
    try:
        path = urlparse(url).path.strip("/")
        parts = path.split("/")
        return parts[-1] if len(parts) > 1 else ""
    except Exception:
        return ""


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

    Special case: when yt-dlp returns multiple entries with the same URL
    (program-level URL bug), each entry is kept separate by ext_id
    and cross-matched to HTML/AJAX entries via slug similarity.
    """
    results: list[DiscoveredEpisode] = []
    by_ext_id: dict[str, DiscoveredEpisode] = {}
    by_url: dict[str, DiscoveredEpisode] = {}

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

    # Detect yt-dlp program-level URL bug: multiple entries sharing the same URL
    ytdlp_urls = [_norm_url_for_merge(ep.url) for ep in ytdlp]
    ytdlp_has_shared_url = len(ytdlp) > 1 and len(set(ytdlp_urls)) == 1

    if ytdlp_has_shared_url:
        # Each yt-dlp entry is a distinct episode despite sharing the program URL.
        # Keep them separate, keyed by ext_id.
        log.info("ytdlp_shared_url_detected", count=len(ytdlp), url=ytdlp_urls[0])
        for ep in ytdlp:
            if ep.ext_id:
                by_ext_id[ep.ext_id] = ep
                results.append(ep)
            else:
                # No ext_id and program-level URL — skip (not downloadable)
                log.debug("ytdlp_skip_no_extid", url=ep.url, title=ep.title)
    else:
        # Normal case: each yt-dlp entry has a unique URL
        for ep in ytdlp:
            norm = _norm_url_for_merge(ep.url)
            by_url[norm] = ep
            if ep.ext_id:
                by_ext_id[ep.ext_id] = ep
            results.append(ep)

    # Build slug index of pending yt-dlp entries (those still on program URL)
    # for cross-matching with HTML/AJAX entries that have episode-level URLs
    ytdlp_by_slug: dict[str, DiscoveredEpisode] = {}
    if ytdlp_has_shared_url:
        for ep in results:
            slug = _slugify(ep.title)
            if slug:
                ytdlp_by_slug[slug] = ep

    def _add_secondary(ep: DiscoveredEpisode):
        """Add an entry from AJAX/HTML/RAPI, merging with existing if matched."""
        # Try ext_id match first
        if ep.ext_id and ep.ext_id in by_ext_id:
            existing = by_ext_id[ep.ext_id]
            # If the existing entry has a program-level URL and this one has
            # an episode-level URL, upgrade the URL
            if ytdlp_has_shared_url and ep.url and ep.url != existing.url:
                existing.url = ep.url
            _enrich(existing, ep)
            return

        norm = _norm_url_for_merge(ep.url)

        # Try URL match
        if norm in by_url:
            _enrich(by_url[norm], ep)
            return

        # Try slug cross-match: compare episode URL slug to yt-dlp title slugs
        if ytdlp_by_slug:
            ep_slug = _url_slug(ep.url)
            if ep_slug:
                best_match = None
                best_ratio = 0
                for ytdlp_slug, ytdlp_ep in ytdlp_by_slug.items():
                    # Check if ep_slug starts with or contains the yt-dlp slug
                    # URL slugs are often truncated versions of the full title
                    if ep_slug.startswith(ytdlp_slug[:30]) or ytdlp_slug.startswith(ep_slug[:30]):
                        # Good enough prefix match
                        best_match = ytdlp_ep
                        break
                    # Fallback: compute overlap ratio
                    from difflib import SequenceMatcher
                    ratio = SequenceMatcher(None, ep_slug, ytdlp_slug).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = ytdlp_ep if ratio > 0.5 else None

                if best_match is not None:
                    # Upgrade URL from program-level to episode-level
                    if ytdlp_has_shared_url:
                        best_match.url = ep.url
                        by_url[norm] = best_match
                        # Remove from slug index to prevent double-matching
                        slug_key = _slugify(best_match.title)
                        ytdlp_by_slug.pop(slug_key, None)
                    _enrich(best_match, ep)
                    return

        # Genuinely new entry
        by_url[norm] = ep
        if ep.ext_id:
            by_ext_id[ep.ext_id] = ep
        results.append(ep)

    # Add secondary sources
    for ep in ajax:
        _add_secondary(ep)
    for ep in html:
        _add_secondary(ep)
    for ep in (rapi or []):
        _add_secondary(ep)

    return results


# ── Public API ────────────────────────────────────────────────────────

def discover_program(
    url: str,
    *,
    skip_ajax: bool = False,
    skip_html: bool = False,
    skip_rapi: bool = False,
    show_name: str | None = None,
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
            rapi_entries = _discover_rapi(original_url, show_name)
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
    ajax_entries: list[DiscoveredEpisode] = []  # layer retired; skip_ajax kept for callers
    html_entries = _discover_html(url) if not skip_html else []

    # RAPI for mujrozhlas URLs too (if original was mujrozhlas, try extracting UUID)
    if not skip_rapi and not rapi_entries and _is_mrz(original_url):
        rapi_entries = _discover_rapi(original_url, show_name)

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
