"""book_meta — normalize a radio book-page title into library metadata.

User rules (2026-07-22, works/113 "Karel je king"):

    "Petr Stančík: Karel je king. Mýty, omyly a pikantnosti ze života
     Karla IV. Čte Vojta Dyk"

    author   = prefix before ':'          → "Petr Stancik"   (unidecoded)
    title    = first sentence after ':'   → "Karel je king"  (unidecoded)
    subtitle = remaining sentences (minus the narrator segment) — original
               diacritics preserved (description keeps them)
    narrator = "Čte/Čtou/Vypráví/Účinkuje X" segment → "Vojta Dyk"
    year     = a clue in the description ("Natočeno v roce 2016") beats the
               broadcast year; broadcast year is the fallback
    genre    = "audiokniha; {program lowercase unidecoded}" (+ topical later)

Everything tag-bound is unidecoded; originals survive in description /
meta_json (the return path).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from unidecode import unidecode

_NARRATOR_RE = re.compile(
    r"(?:^|\.\s*)(?:Čte|Ctou|Čtou|Vypráví|Vypravi|Účinkuje|Ucinkuje|Interpretuje)\s+"
    r"([^.]+?)\s*\.?\s*$",
    re.IGNORECASE,
)
_AUTHOR_PREFIX_RE = re.compile(r"^([^:]{3,60}?):\s+(.+)$", re.S)
_RECORDED_RE = re.compile(r"[Nn]atočen[oa]?\s+v\s+roce\s+(\d{4})")


@dataclass(frozen=True)
class BookMeta:
    author: str | None      # unidecoded
    title: str              # unidecoded, first sentence
    subtitle: str | None    # original diacritics
    narrator: str | None    # unidecoded


def parse_book_title(raw: str) -> BookMeta:
    """Decompose a radio book-page heading into author/title/subtitle/narrator."""
    text = (raw or "").strip()
    author = None
    m = _AUTHOR_PREFIX_RE.match(text)
    if m and not any(ch.isdigit() for ch in m.group(1)) and len(m.group(1).split()) <= 4:
        author = m.group(1).strip()
        text = m.group(2).strip()

    narrator = None
    nm = _NARRATOR_RE.search(text)
    if nm:
        narrator = nm.group(1).strip()
        text = text[: nm.start()].rstrip(" .") if nm.start() > 0 else ""

    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    title = sentences[0].rstrip(".") if sentences else text.rstrip(".")
    subtitle = ". ".join(sentences[1:]).strip() or None

    return BookMeta(
        author=unidecode(author) if author else None,
        title=unidecode(title),
        subtitle=subtitle,
        narrator=unidecode(narrator) if narrator else None,
    )


def year_from_description(description: str | None) -> int | None:
    """A recording-year clue in the perex beats the broadcast year."""
    if not description:
        return None
    m = _RECORDED_RE.search(description)
    return int(m.group(1)) if m else None


def default_genre(program_name: str | None) -> str:
    """'audiokniha; {porad}' — topical genres come from enrichment later."""
    base = "audiokniha"
    if program_name:
        base += f"; {unidecode(program_name).lower().rstrip(' .')}"
    return base
