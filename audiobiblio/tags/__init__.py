"""
audiobiblio.tags — Modular audiobook tag management.

Public API:
    write_tags(path, album_tags, track_tags, cover_path=None)
    read_tags(path) -> dict
    suggest_album_tags(folder_name, existing_tags, filenames) -> dict
    suggest_track_tags(filename, existing_tags, album, author, ...) -> dict
    fix_role_assignment(tags) -> dict
    process_genre(existing_genre, is_english=False) -> str
    strip_diacritics(text) -> str
    generate_suggestions(folder) -> dict  (full CLI-style analysis)
"""
from .writer import write_tags
from .reader import read_tags, find_audio_files, aggregate_album_tags
from .rules import (
    fix_role_assignment,
    suggest_album_tags,
    suggest_track_tags,
    strip_author_from_title,
    fix_track_title_redundancy,
    detect_collection,
)
from .genre import process_genre
from .diacritics import strip_diacritics
from .cli import generate_suggestions

__all__ = [
    "write_tags",
    "read_tags",
    "find_audio_files",
    "aggregate_album_tags",
    "fix_role_assignment",
    "suggest_album_tags",
    "suggest_track_tags",
    "strip_author_from_title",
    "fix_track_title_redundancy",
    "detect_collection",
    "process_genre",
    "strip_diacritics",
    "generate_suggestions",
]
