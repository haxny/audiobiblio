from __future__ import annotations
from pathlib import Path
import re
from unidecode import unidecode

from audiobiblio.core.config import load_config
from audiobiblio.dedupe.matching import is_generic_title


def default_library_root() -> Path:
    cfg = load_config()
    return Path(cfg.library_dir).expanduser()


MAX_STEM_LEN = 80  # max filename stem length (before extension)


def _slug(s: str, max_len: int = 0) -> str:
    """Strip diacritics and make string safe for file/folder names."""
    s = unidecode(s)
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if max_len and len(s) > max_len:
        s = s[:max_len].rstrip(". ")
    return s or "_"


def build_program_folder(ep, work=None) -> str:
    """Return the program folder name for an episode: 'Program (StationCode)'.

    Extracted from build_paths_for_episode so that finalize.py can derive the
    program directory using a caller-supplied library root instead of the
    default from config.
    """
    if work is None:
        work = getattr(ep, "work", None)
    series = getattr(work, "series", None) if work else None
    program = getattr(series, "program", None) if series else None
    station = getattr(program, "station", None) if program else None

    station_code = getattr(station, "code", None) or ""
    program_name = getattr(program, "name", None) or ""

    if program_name and station_code:
        return f"{_slug(program_name)} ({_slug(station_code)})"
    elif program_name:
        return _slug(program_name)
    else:
        return _slug(station_code) if station_code else "Unknown"


def build_paths_for_episode(ep, work=None, info: dict | None = None) -> dict:
    """
    Build final output directory + filename for an episode by walking the DB chain:
        ep.work.series.program.station

    Target layout (flat — no work subfolder):
        {Program} ({station_code})/{Author} - ({year}) {Album} - {01} {episode name}.m4a

    Year and author are encoded in the filename stem, not the directory tree.
    Falls back gracefully when fields are missing.
    Returns: {"base_dir": Path, "stem": str}
    """
    # Resolve DB chain: ep -> work -> series -> program -> station
    if work is None:
        work = getattr(ep, "work", None)

    # --- Extract fields ---
    author = getattr(work, "author", None) or ""
    album = getattr(work, "title", None) or ""
    year = getattr(work, "year", None)
    if not year:
        pub = getattr(ep, "published_at", None)
        if pub:
            year = pub.year

    ep_number = getattr(ep, "episode_number", None)
    _ep_name_raw = getattr(ep, "title", None) or ""
    # Defense-in-depth: treat generic/placeholder titles as absent so they
    # never end up in filename stems (covers existing DB rows not yet cleaned).
    ep_name = "" if is_generic_title(_ep_name_raw) else _ep_name_raw
    # Naming convention (user rule): NO subtitles in filenames — keep only the
    # first sentence of the episode title, and drop it entirely when it just
    # repeats the album (serial parts share the book title; the number is the
    # identity). Prevented stems like
    # "…- 01 Lenka Elbe URaNovA. Jachymov devadesatych let, jeden l.m4a".
    if ep_name:
        ep_name = ep_name.split(". ")[0].rstrip(".")
        from unidecode import unidecode as _ud
        def _n(x):
            return _ud(x or "").lower().strip()
        if album and (_n(ep_name) in _n(album) or _n(album) in _n(ep_name)
                      or _n(ep_name).endswith(_n(album))):
            ep_name = ""

    # --- Build program folder: "Program (StationCode)" ---
    program_folder = build_program_folder(ep, work)

    # --- Build filename stem: "Author - (year) Album - 01 episode name" ---
    # The work info (author, year, album) is folded into the stem instead of
    # a separate subdirectory, keeping the library flat.
    album_s = _slug(album) if album else ""
    author_s = _slug(author) if author else ""
    ep_name_s = _slug(ep_name) if ep_name else ""
    num_s = f"{ep_number:02d}" if ep_number is not None else ""

    # Build the work prefix: "Author - (year) Album"
    if author_s and year:
        work_prefix = f"{author_s} - ({year}) {album_s}"
    elif author_s:
        work_prefix = f"{author_s} - {album_s}"
    elif year:
        work_prefix = f"({year}) {album_s}"
    else:
        work_prefix = album_s

    # Build the episode suffix: "01 episode name"
    if num_s and ep_name_s:
        ep_suffix = f"{num_s} {ep_name_s}"
    elif num_s:
        ep_suffix = num_s
    elif ep_name_s:
        ep_suffix = ep_name_s
    else:
        ep_suffix = ""

    # Combine: "work_prefix - ep_suffix"
    if work_prefix and ep_suffix:
        stem = f"{work_prefix} - {ep_suffix}"
    elif work_prefix:
        stem = work_prefix
    elif ep_suffix:
        stem = ep_suffix
    else:
        stem = "track"

    # Truncate stem to avoid filesystem path length issues.
    # CRITICAL: truncate the WORK PREFIX, never the episode suffix — cutting
    # the tail ate the part number, every part of a long-titled book got the
    # SAME filename, and downloads silently overwrote each other (found live:
    # 24 parts of "Karel je king", one file survived).
    if len(stem) > MAX_STEM_LEN:
        if ep_suffix and work_prefix:
            keep = MAX_STEM_LEN - len(ep_suffix) - 3  # " - "
            if keep >= 8:
                stem = f"{work_prefix[:keep].rstrip('. ')} - {ep_suffix}"
            else:
                stem = ep_suffix[:MAX_STEM_LEN].rstrip(". ")
        else:
            stem = stem[:MAX_STEM_LEN].rstrip(". ")

    root = default_library_root()
    base_dir = root / program_folder

    return {"base_dir": base_dir, "stem": stem}


# --- Legacy helpers (kept for backward compat) ---

def work_dir(author: str | None, title: str) -> Path:
    root = default_library_root()
    author_dir = _slug(author) if author else "_UnknownAuthor"
    title_dir = _slug(title)
    return root / author_dir / title_dir


def episode_file(author: str | None, title: str, episode_number: int | None, episode_title: str, ext: str) -> Path:
    base = work_dir(author, title)
    num = f"{episode_number:02d}" if episode_number is not None else "00"
    fname = f"{num} - {_slug(episode_title)}.{ext}"
    return base / fname
