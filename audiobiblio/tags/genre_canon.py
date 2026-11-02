"""
genre_canon — map genre atoms through a user-approved clean-up table.

The table maps a normalized genre key to its canonical form:
  - a string  → replace (may hold several genres joined by "; ")
  - None      → drop the genre
Genres missing from the table stay exactly as found.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Mapping, Optional

from unidecode import unidecode

_SPLIT = re.compile(r"[;\x00]")


def genre_key(value: str) -> str:
    """Lookup key: ASCII, lowercase, single spaces."""
    return " ".join(unidecode(value).lower().split())


def split_genres(values: Iterable[str]) -> list[str]:
    """Split stored genre values into single genres (on ';' and NUL glue)."""
    return [a.strip() for v in values for a in _SPLIT.split(v or "") if a.strip()]


def apply_genre_map(genres: Iterable[str], mapping: Mapping[str, Optional[str]]) -> list[str]:
    """Return the cleaned genre list; the input is not modified."""
    out: list[str] = []
    seen: set[str] = set()
    for atom in split_genres(genres):
        key = genre_key(atom)
        if key in mapping:
            target = mapping[key]
            parts = split_genres([target]) if target else []
        else:
            parts = [atom]
        for p in parts:
            if genre_key(p) not in seen:
                seen.add(genre_key(p))
                out.append(p)
    return out


def load_genre_map(path: str | Path) -> dict[str, Optional[str]]:
    """Load a {key: target-or-null} JSON table, re-keying defensively."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {genre_key(k): v for k, v in raw.items()}
