"""Work keys: one normalized identity for 'the same work', whatever the
folder or file was called (author-first or surname-first, year, narrator,
length suffixes, subtitles, diacritics, dash variants)."""
from __future__ import annotations

import re

from unidecode import unidecode

SAME_LENGTH_TOLERANCE = 0.02  # ±2 % = same recording

_YEAR = re.compile(r"\(\s*\d{4}\s*\)|\b(19|20)\d{2}\b")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_LEN = re.compile(r"\d+h\d*m|\d+m\d*s?|\d+m\b")
_EXT = re.compile(r"\.(mp3|m4a|m4b|mpga|ogg|opus|flac|aac|wma|zip)$", re.I)
_NON = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str) -> str:
    s = unidecode(s or "").lower()
    s = _EXT.sub("", s)
    s = _PARENS.sub(" ", s)
    s = _LEN.sub(" ", s)
    s = _YEAR.sub(" ", s)
    s = _NON.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_core(title: str) -> str:
    """Main title only: subtitle after the first sentence/colon dropped."""
    t = re.split(r"\.\s|\s[-–]\s|:\s", title, maxsplit=1)[0]
    return _norm(t)


def _author_tokens(author: str) -> str:
    """Order-free author identity: 'Haplova Barbora' == 'Barbora Haplová'."""
    return " ".join(sorted(_norm(author).split()))


def _split_folder(name: str) -> tuple[str | None, str]:
    """'Author - (year) Title …' → (author, title); no separator → (None, name)."""
    parts = re.split(r"\s[-–]\s", name, maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) <= 5:
        return parts[0], parts[1]
    return None, name


def work_key(author: str | None, title: str | None, folder_name: str | None) -> str:
    """'author tokens|title core'. Tags win; the folder name fills the gaps."""
    f_author, f_title = _split_folder(folder_name) if folder_name else (None, "")
    a = author or f_author or ""
    t = title or f_title or ""
    # "Author: Title" titles carry the author prefix
    if ":" in t[:60] and not a:
        a, t = t.split(":", 1)
    return f"{_author_tokens(a)}|{_title_core(t)}"


def classify_pair(dur_a: float | None, dur_b: float | None) -> str:
    """Two copies of one work: same recording, other version, or unknown."""
    if not dur_a or not dur_b:
        return "unknown"
    return "same" if abs(dur_a - dur_b) / max(dur_a, dur_b) <= SAME_LENGTH_TOLERANCE else "version"
