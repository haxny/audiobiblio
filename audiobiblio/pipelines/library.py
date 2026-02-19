from __future__ import annotations
from pathlib import Path
import re
from unidecode import unidecode

from ..config import load_config


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


def build_paths_for_episode(ep, work=None, info: dict | None = None) -> dict:
    """
    Build final output directory + filename for an episode by walking the DB chain:
        ep.work.series.program.station

    Target layout:
        {Program} ({station_code})/{Author} - ({year}) {Album}/{Title} - {01} {episode name}.m4a

    Falls back gracefully when fields are missing.
    Returns: {"base_dir": Path, "stem": str}
    """
    # Resolve DB chain: ep -> work -> series -> program -> station
    if work is None:
        work = getattr(ep, "work", None)
    series = getattr(work, "series", None) if work else None
    program = getattr(series, "program", None) if series else None
    station = getattr(program, "station", None) if program else None

    # --- Extract fields ---
    station_code = getattr(station, "code", None) or ""
    program_name = getattr(program, "name", None) or ""
    author = getattr(work, "author", None) or ""
    album = getattr(work, "title", None) or ""
    year = getattr(work, "year", None)
    if not year:
        pub = getattr(ep, "published_at", None)
        if pub:
            year = pub.year

    title = getattr(work, "title", None) or ""
    ep_number = getattr(ep, "episode_number", None)
    ep_name = getattr(ep, "title", None) or ""

    # --- Build program folder: "Program (StationCode)" ---
    if program_name and station_code:
        program_folder = f"{_slug(program_name)} ({_slug(station_code)})"
    elif program_name:
        program_folder = _slug(program_name)
    else:
        program_folder = _slug(station_code) if station_code else "Unknown"

    # --- Build work folder: "Author - (year) Album" with fallbacks ---
    # At least album must exist for a valid path
    album_s = _slug(album) if album else ""
    author_s = _slug(author) if author else ""

    if author_s and year:
        work_folder = f"{author_s} - ({year}) {album_s}"
    elif author_s:
        work_folder = f"{author_s} - {album_s}"
    elif year:
        work_folder = f"- ({year}) {album_s}"
    else:
        work_folder = album_s or "Unknown Work"

    # --- Build filename stem: "Title - 01 episode name" with fallbacks ---
    title_s = _slug(title) if title else ""
    ep_name_s = _slug(ep_name) if ep_name else ""
    num_s = f"{ep_number:02d}" if ep_number is not None else ""

    if title_s and num_s and ep_name_s:
        stem = f"{title_s} - {num_s} {ep_name_s}"
    elif title_s and ep_name_s:
        stem = f"{title_s} - {ep_name_s}"
    elif title_s and num_s:
        stem = f"{title_s} - {num_s}"
    elif album_s and num_s:
        stem = f"{album_s} - {num_s}"
    elif ep_name_s:
        stem = ep_name_s
    else:
        stem = title_s or album_s or "track"

    # Truncate stem to avoid filesystem path length issues
    if len(stem) > MAX_STEM_LEN:
        stem = stem[:MAX_STEM_LEN].rstrip(". ")

    root = default_library_root()
    base_dir = root / program_folder / work_folder

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
