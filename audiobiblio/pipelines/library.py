from __future__ import annotations
from pathlib import Path
import re
from ..paths import get_dirs

def default_library_root() -> Path:
    # Allow override via env var; fallback to a reasonable default inside user data dir
    import os
    root = os.environ.get("AUDIOBIBLIO_LIBRARY_DIR")
    return Path(root).expanduser() if root else (get_dirs()["data"] / "library")

def _slug(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

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

STATION_NAME_MAP = {
    "radio-junior": "Radio Junior",
    "dvojka": "Dvojka",
    "vltava": "Vltava",
    "plus": "ČRo Plus",
    # extend as needed
}

def _slug_dir(s: str) -> str:
    # folder-safe but keep nice unicode
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "_"

def _slug_file(s: str) -> str:
    # filename-safe; you can keep this same as _slug_dir if you want
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "track"

def _human_station(code: str | None) -> str:
    if not code:
        return "Český rozhlas"
    return STATION_NAME_MAP.get(code.lower(), code)

def build_paths_for_episode(ep, work, info: dict | None = None) -> dict:
    """
    Decide final output directory + filename stem based on:
      - Station (from info['channel_id'] if present; fallback to DB fields if you have them)
      - Program (best name you have for the parent show)
      - Author (from HTML/DB if available, else _UnknownAuthor)
      - Series vs. album (multi-episode vs single)
    Returns: {"base_dir": Path, "stem": str}
    """
    info = info or {}

    # Station
    station_code = info.get("channel_id") or getattr(work, "station_code", None) or getattr(ep, "station_code", None)
    station_name = _human_station(station_code)

    # Program (parent show name)
    program_name = getattr(work, "program_name", None) or getattr(work, "title", None) or "Program"

    # Author
    author = (info.get("author")
              or getattr(ep, "author", None)
              or getattr(work, "author", None)
              or "_UnknownAuthor")

    # Series vs album
    is_series = bool(getattr(work, "is_series", None) or getattr(ep, "episode_number", None) is not None)

    if is_series:
        series_title = getattr(work, "title", None) or program_name
        leaf_dir = f"{author} – {series_title}"
        # stem like "01 Title"
        track_no = (getattr(ep, "episode_number", None) or 1)
        stem = f"{track_no:02d} {_slug_file(getattr(ep, 'title', None) or 'Track')}"
    else:
        album_title = getattr(ep, "title", None) or getattr(work, "title", None) or "Album"
        leaf_dir = f"{author} – {album_title}"
        stem = _slug_file(leaf_dir)

    root = default_library_root()
    base_dir = (root
                / _slug_dir(station_name)
                / _slug_dir(program_name)
                / _slug_dir(leaf_dir))

    return {"base_dir": base_dir, "stem": stem}