"""
dedupe — Content-aware deduplication for discovered episodes.

Three tiers:
1. ext_id match — same UUID = same episode
2. URL normalization — strip trailing numeric suffixes (-2941669), normalize host/scheme
3. Title normalization — strip diacritics, strip series prefix, fuzzy match (ratio > 0.9).
   Guard: fuzzy match is suppressed when both entries carry distinct non-empty
   stripped URLs (multi-part serialized books share titles).
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import structlog

from audiobiblio.core.urls import norm_url as _norm_url, norm_url_strip_reair as _norm_url_strip_reair

log = structlog.get_logger()

# Generic/placeholder titles that should NOT trigger fuzzy dedup and must
# never become stored episode titles, filename stems, or audio tags.
# All entries are in normalised form: lowercase, no diacritics, single spaces.
GENERIC_TITLES: frozenset[str] = frozenset({
    "epizody poradu",   # mujrozhlas: "Epizody pořadu"
    "episodes",         # English placeholder
    "vsechny dily",     # "všechny díly" — "all episodes" in Czech
    "all episodes",     # English long-form
})

# Backward-compatible private alias (internal code used this name).
_GENERIC_TITLES = GENERIC_TITLES


@dataclass
class DuplicateGroup:
    """A group of episodes identified as duplicates."""
    canonical_url: str
    canonical_title: str
    duplicates: list[dict] = field(default_factory=list)  # [{url, title, reason}]


def _strip_diacritics(s: str) -> str:
    """Remove diacritical marks from a string."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_title(title: str | None, series_prefix: str | None = None) -> str:
    """Normalize a title for fuzzy matching."""
    if not title:
        return ""
    t = title.strip()
    # Strip series prefix
    if series_prefix:
        prefix_norm = series_prefix.strip()
        for sep in (":", " -", " –", " —"):
            full_prefix = prefix_norm + sep
            if t.startswith(full_prefix):
                t = t[len(full_prefix):].strip()
                break
    # Lowercase and strip diacritics
    t = _strip_diacritics(t.lower())
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_generic_title(title: str) -> bool:
    """Return True if *title* is a known generic/placeholder episode title.

    Normalises the input (diacritics stripped, lowercased, whitespace
    collapsed) before checking against GENERIC_TITLES, so callers need not
    pre-normalise.  An empty string always returns False — callers should
    treat absence (empty/None) as "no title", not as "generic title".

    Examples::

        is_generic_title("Epizody pořadu")  # True  — mujrozhlas leak
        is_generic_title("epizody poradu")  # True
        is_generic_title("Kapitola 1")      # False
        is_generic_title("")                # False
    """
    if not title or not title.strip():
        return False
    return _norm_title(title) in GENERIC_TITLES


def dedupe_discovered(
    entries: list,
    existing_episodes: list | None = None,
    series_prefix: str | None = None,
) -> tuple[list, list[DuplicateGroup]]:
    """
    Content-aware deduplication.

    Args:
        entries: list of DiscoveredEpisode (or anything with .url, .title, .ext_id)
        existing_episodes: optional list of DB Episode objects to check against
        series_prefix: series name to strip from titles for comparison

    Returns:
        (unique_entries, duplicate_groups)
    """
    unique: list = []
    duplicate_groups: list[DuplicateGroup] = []

    # Indices for fast lookup
    seen_ext_ids: dict[str, int] = {}  # ext_id -> index in unique
    seen_urls: dict[str, int] = {}  # normalized URL -> index in unique
    seen_urls_stripped: dict[str, int] = {}  # URL with reair suffix stripped -> index
    seen_titles: dict[str, tuple[int, str]] = {}  # normalized title -> (index in unique, stripped_url)

    # Pre-populate with existing DB episodes
    if existing_episodes:
        for ep in existing_episodes:
            idx = -1  # sentinel for "exists in DB"
            ext_id = getattr(ep, "ext_id", None)
            if ext_id:
                seen_ext_ids[ext_id] = idx
            url = getattr(ep, "url", None)
            if url:
                seen_urls[_norm_url(url)] = idx
                seen_urls_stripped[_norm_url_strip_reair(url)] = idx

    for entry in entries:
        ext_id = getattr(entry, "ext_id", None)
        url = getattr(entry, "url", None)
        title = getattr(entry, "title", None)

        norm_url = _norm_url(url)
        stripped_url = _norm_url_strip_reair(url)
        norm_title = _norm_title(title, series_prefix)

        dup_reason = None
        dup_target_idx = None

        # Tier 1: ext_id match
        if ext_id and ext_id in seen_ext_ids:
            dup_reason = "ext_id"
            dup_target_idx = seen_ext_ids[ext_id]

        # Tier 2a: exact URL match
        elif norm_url and norm_url in seen_urls:
            dup_reason = "url_exact"
            dup_target_idx = seen_urls[norm_url]

        # Tier 2b: URL match after stripping re-air suffix
        elif stripped_url and stripped_url in seen_urls_stripped:
            dup_reason = "url_reair"
            dup_target_idx = seen_urls_stripped[stripped_url]

        # Tier 3: fuzzy title match (skip generic/placeholder titles)
        elif norm_title and len(norm_title) > 5 and norm_title not in _GENERIC_TITLES:
            for seen_t, (idx, seen_stripped_url) in seen_titles.items():
                if SequenceMatcher(None, norm_title, seen_t).ratio() > 0.9:
                    # Guard: if both entries carry distinct URLs they are separate
                    # episodes (e.g. multi-part books with identical chapter titles).
                    # Urlless entries still collapse; re-air pairs with the same
                    # stripped URL are already caught by tier 2b before reaching here.
                    if stripped_url and seen_stripped_url and stripped_url != seen_stripped_url:
                        continue
                    dup_reason = "title_fuzzy"
                    dup_target_idx = idx
                    break

        if dup_reason:
            # Record duplicate
            if dup_target_idx is not None and dup_target_idx >= 0:
                canonical = unique[dup_target_idx]
                group = DuplicateGroup(
                    canonical_url=getattr(canonical, "url", ""),
                    canonical_title=getattr(canonical, "title", ""),
                )
                group.duplicates.append({
                    "url": url or "",
                    "title": title or "",
                    "reason": dup_reason,
                })
                duplicate_groups.append(group)
            else:
                # Duplicate of existing DB episode
                duplicate_groups.append(DuplicateGroup(
                    canonical_url="(existing in DB)",
                    canonical_title="",
                    duplicates=[{"url": url or "", "title": title or "", "reason": dup_reason}],
                ))
            log.debug("dedupe_skip", url=url, reason=dup_reason)
            continue

        # Not a duplicate — add to unique
        idx = len(unique)
        unique.append(entry)

        if ext_id:
            seen_ext_ids[ext_id] = idx
        if norm_url:
            seen_urls[norm_url] = idx
        if stripped_url:
            seen_urls_stripped[stripped_url] = idx
        if norm_title:
            seen_titles[norm_title] = (idx, stripped_url)

    log.info(
        "dedupe_result",
        total=len(entries),
        unique=len(unique),
        duplicates=len(duplicate_groups),
    )
    return unique, duplicate_groups
