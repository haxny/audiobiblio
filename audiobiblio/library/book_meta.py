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


_NAME_TOKEN_RE = re.compile(r"^[A-ZÀ-ŽČĎĚŇŘŠŤŮŽ][\w'’.\-]*$", re.UNICODE)
_NAME_PARTICLES = {"de", "van", "von", "der", "da", "di", "le", "la", "ml.", "st.", "ml", "st",
                   "mladsi", "starsi", "mladší", "starší"}
_NAME_SPLIT_RE = re.compile(r"\s*(?:,|;|&|\s+a\s+)\s*")
# credits glued onto the cast line ("Josef PejchalProdukce: …Natočeno: 2023")
_CREDIT_CUT_RE = re.compile(
    r"(Připravil|Pripravil|Režie|Rezie|Natočeno|Natoceno|Překlad|Preklad|Produkce|"
    r"Technick|Mistr zvuku|Pořad|Porad|Dramaturgie|Hudba|Premiéra|Premiera|Sbor|"
    r"Poslouchejte|Účinkuj|Ucinkuj)")
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_AND_OTHERS_RE = re.compile(r"[,\s]+a\s+(další|dalsi|další\.|dalsi\.)\s*$", re.I)


def _is_name(name: str) -> bool:
    tokens = name.split()
    if not 2 <= len(tokens) <= 4 or not _NAME_TOKEN_RE.match(tokens[0]):
        return False
    return all(_NAME_TOKEN_RE.match(t) or t.lower() in _NAME_PARTICLES for t in tokens)


def _trim_name(name: str) -> str:
    """'Lucie Vavrickova ze Statniho archivu' → 'Lucie Vavrickova': keep the
    leading capitalised tokens, stop at the first plain lowercase word."""
    out: list[str] = []
    for t in name.split():
        if _NAME_TOKEN_RE.match(t) or (out and t.lower() in _NAME_PARTICLES):
            out.append(t)
        else:
            break
    return " ".join(out) if len(out) >= 2 else name


def _strip_dot(name: str) -> str:
    """Drop a sentence-final dot, keep the 'st.'/'ml.' generation suffix."""
    if name.endswith(".") and name.split()[-1].lower() not in ("st.", "ml."):
        return name[:-1]
    return name


def clean_person_names(value: str | None) -> str | None:
    """Narrator/cast value → clean "Name, Name a Name" or None.

    Strips role parentheticals, "| Station" suffixes, "a další" and credits
    glued on ("…Připravil: X Režie: Y"). Names that do not read as names
    are dropped; a value whose FIRST name is not a name is a sentence
    fragment ("narsky denik" from "Čtenářský deník", "o svém dětství…")
    and yields None. 1,252 episodes carried such fragments (2026-10-09).
    """
    if not value or any(ord(ch) < 32 for ch in value):
        return None
    v = value.split("|")[0]
    m = _CREDIT_CUT_RE.search(v)
    if m:
        v = v[:m.start()]
    v = _PAREN_RE.sub("", v)
    v = re.split(r"\.\s+(?=[A-ZÀ-Ž][a-zà-ž]+\s+[a-zà-ž])", v)[0]  # trailing sentence
    others = bool(_AND_OTHERS_RE.search(v.strip()))
    v = _AND_OTHERS_RE.sub("", v.strip()).strip().rstrip(",:;")
    names = [_strip_dot(n.strip()) for n in _NAME_SPLIT_RE.split(v) if n.strip()]
    if not names or not _NAME_TOKEN_RE.match(names[0].split()[0]):
        return None
    kept = [t for t in (_trim_name(n) for n in names) if _is_name(t)]
    if not kept or kept[0] != _trim_name(names[0]):
        return None
    original = value.strip().rstrip(",:;")
    raw = [_strip_dot(n.strip()) for n in _NAME_SPLIT_RE.split(
        _AND_OTHERS_RE.sub("", original).strip()) if n.strip()]
    if kept == raw:
        return original  # already clean — keep the user-visible form as is
    joined = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + " a " + kept[-1]
    if others:
        joined = ", ".join(kept) + " a dalsi"
    return joined


def looks_like_person_names(value: str | None) -> bool:
    """True when `value` is already a clean name list — cleaning would drop
    nothing (separators "," / "a" are equivalent)."""
    cleaned = clean_person_names(value)
    if cleaned is None:
        return False
    split = lambda v: [n for n in _NAME_SPLIT_RE.split(v.strip()) if n]
    return split(cleaned) == split(value)
