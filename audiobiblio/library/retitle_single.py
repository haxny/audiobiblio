"""Fix album / title / track tags of single-file stories already on the shelf.

Only when we are 100 % sure: the work has exactly one episode, no serial UUID,
expected_total is 1 or unknown, the file is the ONLY audio file in a curated
shelf folder (finalize never shelves an incomplete book), the artist tag still
equals the work author, and the cleaned title is contained in the current album
(we shorten, we never rename into something else). Everything else is reported
as skipped with the reason and left alone.
"""
from __future__ import annotations

import re
from pathlib import Path

from unidecode import unidecode

AUDIO_EXT = {".m4a", ".m4b", ".mp3", ".mp4", ".aac", ".flac", ".ogg", ".opus"}


def _norm(s: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unidecode(s or "").lower()).split())


def clean_tag_title(title: str | None, author: str | None) -> str:
    """Tag form of a book title: no 'Author: ' prefix, no teaser, no diacritics."""
    from audiobiblio.library.pipelines.finalize import clean_book_title

    return unidecode(clean_book_title(title, author) or "").strip()


def decide(current: dict, artist_tag: str | None, work_title: str, author: str | None,
           cleaned: str) -> tuple[dict | None, str]:
    """-> (new tags or None, reason). Pure: easy to test."""
    if not (author or "").strip() or not cleaned:
        return None, "no author or empty cleaned title"
    if _norm(artist_tag) != _norm(author):
        return None, "artist tag differs from work author (manual edit?)"
    if _norm(cleaned) not in _norm(current.get("album")):
        return None, "cleaned title not contained in current album"
    # a track title equal to the album says nothing: leave it empty (user rule)
    new = {"album": cleaned, "title": None, "track": None}
    if current == new:
        return None, "already clean"
    return new, "ok"


def audio_files_in(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT]
