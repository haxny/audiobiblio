"""
rules — Pure-function tag correction rules for audiobooks.

All functions are pure (no file I/O, no side effects) — only transform dicts.
"""
from __future__ import annotations
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import structlog

from .diacritics import strip_diacritics, apply_czech_parts_replacement
from .genre import process_genre

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Role correction (TAG_ROLE_FIXES.md)
# ---------------------------------------------------------------------------

def fix_role_assignment(tags: Dict[str, str]) -> Dict[str, str]:
    """
    Fix common role assignment mistakes in audiobook tags.

    Rule 1: Narrator in AlbumArtist → move to Performer, set AlbumArtist = Artist
    Rule 2: Author in Composer → move to Artist/AlbumArtist, clear Composer
    Rule 3: Author name prefix in Album title → strip it
    """
    fixed = tags.copy()

    performer = fixed.get("performer", "").strip()
    artist = fixed.get("artist", "").strip()
    albumartist = fixed.get("albumartist", "").strip()

    # Rule 1: Narrator wrongly in Album Artist
    if (not performer or performer == "n/a") and albumartist and artist:
        if albumartist != artist and albumartist not in ("Various Artists", "n/a", ""):
            log.info("role_swap_detected",
                     issue="Narrator in Album Artist",
                     moving_to_performer=albumartist,
                     setting_albumartist_to=artist)
            fixed["performer"] = albumartist
            fixed["albumartist"] = artist

    # Rule 2: Author in Composer
    composer = fixed.get("composer", "").strip()
    if composer and composer != "n/a":
        if (not artist or artist in ("n/a", "", "Unknown")) and composer:
            log.info("role_swap_detected",
                     issue="Author in Composer",
                     moving_to_artist=composer)
            fixed["artist"] = composer
            fixed["albumartist"] = composer
            fixed["composer"] = "n/a"

    # Rule 3: Author name in album title
    artist_clean = fixed.get("artist", "").strip()
    album = fixed.get("album", "").strip()
    if artist_clean and album:
        for sep in (" - ", ": ", " – ", " — "):
            prefix = f"{artist_clean}{sep}"
            if album.startswith(prefix):
                album_cleaned = album[len(prefix):]
                log.info("album_cleaned", old_album=album, new_album=album_cleaned)
                fixed["album"] = album_cleaned
                break

    return fixed


# ---------------------------------------------------------------------------
# Author / collection detection
# ---------------------------------------------------------------------------

def extract_author_from_folder(folder_name: str) -> Optional[str]:
    """Extract author from folder patterns like 'Author [audio]'."""
    match = re.match(r"^(.+?)\s*\[.+\]$", folder_name)
    return match.group(1).strip() if match else None


def detect_author_in_filenames(files: List[str]) -> Optional[str]:
    """
    Detect author name that appears consistently in filenames.
    Patterns: "X. díl; Author; Title" or "X; Author; Title"
    """
    if len(files) < 2:
        return None

    dil_pattern = re.compile(r"^\d+\.\s*díl;\s*([^;]+);\s*(.+)$", re.IGNORECASE)
    simple_pattern = re.compile(r"^\d+;\s*([^;]+);\s*(.+)$")
    authors = []

    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        match = dil_pattern.match(stem) or simple_pattern.match(stem)
        if match:
            authors.append(match.group(1).strip())

    if len(authors) < len(files) // 2 or not authors:
        return None

    most_common, count = Counter(authors).most_common(1)[0]
    return most_common if count >= len(authors) * 0.8 else None


def detect_collection(folder_name: str, files: List[str]) -> bool:
    """Detect if folder is a collection (each file is a separate work)."""
    if len(files) <= 1:
        return False
    if extract_author_from_folder(folder_name):
        return True
    return detect_author_in_filenames(files) is not None


# ---------------------------------------------------------------------------
# Title parsing helpers
# ---------------------------------------------------------------------------

def strip_author_from_title(title: str, author: str) -> str:
    """Remove author prefix from title (handles diacritics and bracket variants)."""
    if not author or not title:
        return title

    author_normalized = strip_diacritics(author).lower().replace(",", "").strip()

    # Try direct separators
    for sep in ("; ", ";", ": ", ":", " - "):
        prefix = f"{author}{sep}"
        if title.startswith(prefix):
            cleaned = title[len(prefix):].strip()
            if cleaned:
                return cleaned

    # Try with brackets/parentheses
    for ws, we in (("[", "]"), ("(", ")")):
        for sep in (" - ", ": ", " – ", " — "):
            prefix = f"{ws}{author}{we}{sep}"
            if title.startswith(prefix):
                cleaned = title[len(prefix):].strip()
                if cleaned:
                    return cleaned

            # Normalized match for diacritic variations
            esc_ws = re.escape(ws)
            if we == "]":
                pat = esc_ws + r"([^\]]+)" + re.escape(we) + re.escape(sep)
            else:
                pat = esc_ws + r"([^)]+)" + re.escape(we) + re.escape(sep)

            match = re.match(pat, title)
            if match:
                bracketed = match.group(1)
                bn = strip_diacritics(bracketed).lower().replace(",", "").strip()
                if bn == author_normalized or set(bn.split()) == set(author_normalized.split()):
                    cleaned = title[match.end():].strip()
                    if cleaned:
                        return cleaned
    return title


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for flexible comparison (dots/spaces/case)."""
    if not text:
        return ""
    return " ".join(text.replace(".", " ").replace("_", " ").split()).lower()


def fix_track_title_redundancy(title: str, album: str, author: str = "") -> str:
    """Remove album name (or author+album) from track title when redundant."""
    if not title or not album:
        return title

    # Standard prefix removal
    for sep in (" - ", ": ", " – ", " — "):
        prefix = f"{album}{sep}"
        if title.startswith(prefix):
            return title[len(prefix):]

    # Check if entire title equals "author album"
    title_norm = _normalize_for_comparison(title)
    album_norm = _normalize_for_comparison(album)

    if author:
        author_norm = _normalize_for_comparison(author)
        if title_norm == f"{author_norm} {album_norm}":
            return ""

    if title_norm == album_norm:
        return ""

    # Check suffix match (≥10 chars to avoid false positives)
    title_clean = strip_diacritics(_normalize_for_comparison(title))
    album_clean = strip_diacritics(_normalize_for_comparison(album))
    if title_clean and album_clean.endswith(title_clean) and len(title_clean) >= 10:
        return ""

    return title


def normalize_track_number(tn: str) -> str:
    """Normalize track numbers: '1 of 3' → '1', '01/12' → '1', '03' → '3'."""
    if not tn or tn == "n/a":
        return tn
    # Handle "X of Y", "X/Y" formats
    m = re.match(r'^(\d+)\s*(?:of|/)\s*\d+', tn, re.IGNORECASE)
    if m:
        return str(int(m.group(1)))
    # Handle plain numbers
    m = re.match(r'^(\d+)', tn)
    if m:
        return str(int(m.group(1)))
    return tn


def normalize_date(date_str: str) -> str:
    """
    Normalize date to YYYYMMDD or YYYY format.
    '2025:12:06' → '20251206', '2025-12-06' → '20251206', '20251206' → '20251206', '2025' → '2025'
    """
    if not date_str or date_str == "n/a":
        return date_str
    # Full date with separators: YYYY:MM:DD, YYYY-MM-DD, YYYY/MM/DD
    m = re.match(r'^(\d{4})[:/-](\d{2})[:/-](\d{2})', date_str)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # Already YYYYMMDD or just YYYY
    m = re.match(r'^(\d{4,8})', date_str)
    return m.group(1) if m else date_str


def parse_dil_filename(stem: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Parse filename with díl patterns.

    Returns: (part_number, author, work_title)
    """
    # "X. díl; Author; Title"
    m = re.match(r"^(\d+)\.\s*díl;\s*([^;]+);\s*(.+)$", stem, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

    # "X. díl; Title" (no author)
    m = re.match(r"^(\d+)\.\s*díl;\s*(.+)$", stem, re.IGNORECASE)
    if m:
        return m.group(1).strip(), None, m.group(2).strip()

    # "X; Author; Title"
    m = re.match(r"^(\d+);\s*([^;]+);\s*(.+)$", stem)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

    return None, None, stem


def parse_short_story_filename(filename_title: str, author: str) -> Tuple[str, str]:
    """Parse short story filename → (work_title, subtitle). Double-space separates subtitle."""
    if not author or not filename_title:
        return filename_title, ""

    title_without_author = strip_author_from_title(filename_title, author)

    if "  " in title_without_author:
        parts = title_without_author.split("  ", 1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""

    for sep in (". ", " - "):
        if sep in title_without_author:
            parts = title_without_author.split(sep, 1)
            if len(parts[0]) > 3:
                return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""

    return title_without_author, ""


def detect_generic_filename(suggested_title: str, author: str, album: str) -> bool:
    """Detect if a filename-derived title is generic (just author+album, no chapter info)."""
    if not suggested_title or not suggested_title.strip():
        return True
    if not author or not album:
        return False

    title_normalized = strip_diacritics(suggested_title).lower()
    author_normalized = strip_diacritics(author).lower().replace(",", "").replace("  ", " ")
    album_normalized = strip_diacritics(album).lower()

    title_clean = re.sub(r'[\[\]()_-]', ' ', title_normalized).strip()
    title_clean = ' '.join(title_clean.split())

    author_parts = author_normalized.split()
    author_parts_in_title = all(p in title_clean for p in author_parts if len(p) > 2)

    album_words = album_normalized.split()
    album_significant = ' '.join(album_words[-3:]) if len(album_words) >= 3 else album_normalized
    album_in_title = album_significant in title_clean.replace('-', ' ')

    return author_parts_in_title and album_in_title


# ---------------------------------------------------------------------------
# Album-level suggestion
# ---------------------------------------------------------------------------

def suggest_album_tags(
    folder_name: str,
    existing_tags: Dict[str, str],
    filenames: List[str] | None = None,
    *,
    strip_diacritics_flag: bool = True,
) -> Dict[str, str]:
    """
    Suggest album-level tags from folder name and existing tags.

    Handles patterns:
    - "Author [audio]"
    - "Author - (Year) Album"
    - "Author - Album"
    """
    fixed_tags = fix_role_assignment(existing_tags)
    suggestions: Dict[str, str] = {}

    extracted_author = extract_author_from_folder(folder_name)

    if not extracted_author:
        # "Author - (YYYY) Album"
        m = re.match(r"^(.+?)\s*-\s*\((\d{4})\)\s*(.+)$", folder_name)
        if m:
            extracted_author = m.group(1).strip()
            year = m.group(2).strip()
            album_title = m.group(3).strip()
            if strip_diacritics_flag:
                suggestions["artist"] = strip_diacritics(extracted_author)
                suggestions["albumartist"] = strip_diacritics(extracted_author)
                suggestions["album"] = strip_diacritics(album_title)
            else:
                suggestions["artist"] = extracted_author
                suggestions["albumartist"] = extracted_author
                suggestions["album"] = album_title
            suggestions["date"] = year
        else:
            # "Author - Album"
            m = re.match(r"^(.+?)\s*-\s*(.+)$", folder_name)
            if m:
                extracted_author = m.group(1).strip()
                album_title = m.group(2).strip()
                if strip_diacritics_flag:
                    suggestions["artist"] = strip_diacritics(extracted_author)
                    suggestions["albumartist"] = strip_diacritics(extracted_author)
                    suggestions["album"] = strip_diacritics(album_title)
                else:
                    suggestions["artist"] = extracted_author
                    suggestions["albumartist"] = extracted_author
                    suggestions["album"] = album_title

    if extracted_author and "artist" not in suggestions:
        suggestions["artist"] = strip_diacritics(extracted_author) if strip_diacritics_flag else extracted_author
        suggestions["albumartist"] = suggestions["artist"]
        suggestions["album"] = fixed_tags.get("album", suggestions["artist"])

    # Fallbacks
    if "artist" not in suggestions:
        suggestions["artist"] = fixed_tags.get("artist", "n/a")
    if "albumartist" not in suggestions:
        suggestions["albumartist"] = fixed_tags.get(
            "albumartist",
            strip_diacritics(fixed_tags.get("artist", "Various Artists")) if strip_diacritics_flag else fixed_tags.get("artist", "Various Artists"),
        )
    if "album" not in suggestions:
        suggestions["album"] = fixed_tags.get(
            "album",
            strip_diacritics(folder_name) if strip_diacritics_flag else folder_name,
        )

    suggestions["performer"] = fixed_tags.get("performer", "n/a")
    suggestions["translator"] = fixed_tags.get("translator", "n/a")
    suggestions["publisher"] = fixed_tags.get("publisher", "n/a")

    # Genre — detect English content
    is_english = False
    artist_lower = (suggestions.get("artist") or "").lower()
    album_lower = (suggestions.get("album") or "").lower()
    if any(ind in artist_lower or ind in album_lower
           for ind in ("[audio]", "(audio)", "audiobook", "narrated by")):
        if not any(c in album_lower for c in "áčďéěíňóřšťúůýž"):
            is_english = True

    suggestions["genre"] = process_genre(fixed_tags.get("genre", ""), is_english=is_english)

    if "date" not in suggestions:
        suggestions["date"] = normalize_date(fixed_tags.get("date", "n/a"))

    suggestions["discnumber"] = fixed_tags.get("discnumber", "n/a")
    suggestions["comment"] = fixed_tags.get("comment", "n/a")
    suggestions["description"] = fixed_tags.get("description", "n/a")
    suggestions["www"] = fixed_tags.get("www", "n/a")

    # Strip diacritics from all values (except URLs and comments)
    if strip_diacritics_flag:
        for k, v in suggestions.items():
            if v != "n/a" and k not in ("www", "comment", "description"):
                suggestions[k] = strip_diacritics(v)

    return suggestions


# ---------------------------------------------------------------------------
# Track-level suggestion
# ---------------------------------------------------------------------------

def suggest_track_tags(
    filename: str,
    existing_tags: Dict[str, str],
    album: str = "",
    author: str = "",
    *,
    is_single_file: bool = False,
    is_collection: bool = False,
    strip_diacritics_flag: bool = True,
) -> Dict[str, str]:
    """
    Suggest track-level tags based on filename (source of truth for Czech diacritics).
    """
    suggestions: Dict[str, str] = {}
    stem = os.path.splitext(os.path.basename(filename))[0]

    # Strip UUID suffixes like [7485acbc-fb2d-4c07-8b61-b338d484eea8]
    stem = re.sub(r'\s*\[[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\]$', '', stem).strip()

    # Check díl patterns first
    part_num, detected_author, work_title = parse_dil_filename(stem)

    if part_num and detected_author and work_title:
        # Collection: "X. díl; Author; Title"
        title = strip_diacritics(work_title) if strip_diacritics_flag else work_title
        title = apply_czech_parts_replacement(title)
        suggestions["title"] = title
        suggestions["album"] = title
        suggestions["tracknumber"] = part_num
        return suggestions

    if part_num and not detected_author and work_title:
        # Single work split into parts
        wt_norm = strip_diacritics(work_title).lower()
        album_norm = album.lower() if album else ""
        if album and wt_norm == album_norm:
            suggestions["title"] = ""
        else:
            suggestions["title"] = strip_diacritics(work_title) if strip_diacritics_flag else work_title
        suggestions["tracknumber"] = part_num
        return suggestions

    # Standard: extract title from filename
    # Strip album prefix if present (e.g., "Album - 01 Title" → "01 Title")
    working_stem = stem
    if album:
        for sep in (" - ", " – ", " — ", ": "):
            album_prefix = f"{album}{sep}"
            if working_stem.startswith(album_prefix):
                working_stem = working_stem[len(album_prefix):]
                break
            # Also try without diacritics
            album_stripped = strip_diacritics(album) if strip_diacritics_flag else album
            stem_stripped = strip_diacritics(working_stem) if strip_diacritics_flag else working_stem
            if stem_stripped.lower().startswith(f"{album_stripped.lower()}{sep.lower()}"):
                working_stem = working_stem[len(album_prefix):]
                break

    # Strip leading track number (e.g., "01 Title", "01. Title", "01- Title")
    filename_title = re.sub(r"^\d+[.\s\-]+", "", working_stem).strip()
    suggested_title = filename_title
    suggested_comment = ""

    if is_collection and author:
        work_title, subtitle = parse_short_story_filename(filename_title, author)
        suggested_title = work_title
        suggested_comment = subtitle
        if strip_diacritics_flag:
            work_title_clean = strip_diacritics(work_title)
            suggested_comment = strip_diacritics(subtitle) if subtitle else ""
        else:
            work_title_clean = work_title
        suggestions["album"] = work_title_clean
        if suggested_comment:
            suggestions["comment"] = suggested_comment
    else:
        author_for_cleaning = author or existing_tags.get("artist", "") or existing_tags.get("albumartist", "")
        if author_for_cleaning:
            suggested_title = strip_author_from_title(suggested_title, author_for_cleaning)
        if album and not is_single_file:
            suggested_title = fix_track_title_redundancy(suggested_title, album, author_for_cleaning)

    # Generic filename detection — preserve existing title if it has chapter info
    existing_title = existing_tags.get("title", "").strip()
    if detect_generic_filename(suggested_title, author, album):
        if existing_title and existing_title != album:
            cleaned = re.sub(r"^\d+[.\s\-]+", "", existing_title).strip()
            cleaned = fix_track_title_redundancy(cleaned, album, author)
            cleaned = re.sub(r"^(\d{4})\s*-\s*", r"\1 ", cleaned)
            if strip_diacritics_flag:
                cleaned = strip_diacritics(cleaned)
            suggestions["title"] = cleaned
            if existing_tags.get("tracknumber"):
                suggestions["tracknumber"] = normalize_track_number(existing_tags["tracknumber"])
            else:
                m = re.match(r"^(\d+)", os.path.basename(filename))
                if m:
                    suggestions["tracknumber"] = m.group(1).lstrip("0") or "0"
            return suggestions

    if strip_diacritics_flag:
        suggested_title = strip_diacritics(suggested_title)
        if suggested_comment:
            suggested_comment = strip_diacritics(suggested_comment)

    suggested_title = apply_czech_parts_replacement(suggested_title)
    suggestions["title"] = suggested_title

    if existing_tags.get("tracknumber"):
        suggestions["tracknumber"] = normalize_track_number(existing_tags["tracknumber"])
    else:
        m = re.match(r"^(\d+)", os.path.basename(filename))
        suggestions["tracknumber"] = (m.group(1).lstrip("0") or "0") if m else "n/a"

    return suggestions
