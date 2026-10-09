"""Enrich audiobiblio works from the mluvenypanacek.cz index (user rule:
use the catalog instead of guessing; empty → fill, conflict → proposal).

match_work(): exact title (author prefix stripped) + at least one of
author / narrator agreement or a unique title; ties → ambiguous, untouched.
plan_work(): per field (author, narrator, recorded_year, parts_total)
  fill | same | conflict — never overwrites, conflicts go to the user.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.core.db.models import FieldOrigin, MetadataValue, Work
from audiobiblio.library.sync import compute_resolved
from audiobiblio.sources.panacek_index import find

MIN_SCORE = 5.0


def _n(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", unidecode(s or "").lower())).strip()


def split_title(title: str, author: str | None) -> tuple[str, str | None]:
    """'Alois Jirásek: Staré pověsti české. Podtitul' → ('Staré pověsti české', 'Alois Jirásek')."""
    t, a = title or "", author
    if ":" in t[:70]:
        head, tail = t.split(":", 1)
        if len(head.split()) <= 5:
            a, t = a or head.strip(), tail.strip()
    t = re.split(r"\.\s", t, maxsplit=1)[0].strip().rstrip(".")
    return t, a


@dataclass(frozen=True)
class Match:
    record: dict | None
    verdict: str          # matched | ambiguous | none


def match_work(con: sqlite3.Connection, title: str, author: str | None, narrator: str | None,
               minutes: float | None) -> Match:
    hits = find(con, title, author=author, narrator=narrator, minutes=minutes, limit=5)
    exact = [h for h in hits if _n(h["title_base"]) == _n(title)]
    if not exact:
        return Match(None, "none")
    best = exact[0]
    if len(exact) > 1 and exact[1]["score"] == best["score"]:
        return Match(None, "ambiguous")      # same evidence for two works: never guess
    unique_title = len(exact) == 1
    if best["score"] < MIN_SCORE and not (unique_title and best["score"] >= 3):
        return Match(None, "none")
    return Match(best, "matched")


def _ascii(names: list[str]) -> str | None:
    if not names:
        return None
    names = [unidecode(n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " a " + names[-1]


def proposed_values(rec: dict) -> dict:
    authors = json.loads(rec["authors"])
    narrators = json.loads(rec["narrators"])
    years = json.loads(rec["recorded_years"])
    return {
        "author": _ascii(authors),
        "narrator": _ascii(narrators[:3]) if len(narrators) <= 3 else _ascii(narrators[:2]) + " a dalsi",
        "recorded_year": str(min(years)) if years else None,
        "parts_total": str(rec["parts_total"]) if rec["parts_total"] else None,
    }


def plan_work(session: Session, con: sqlite3.Connection, work: Work) -> dict:
    eps = sorted(work.episodes, key=lambda e: (e.episode_number or 0, e.id))
    r = compute_resolved(session, eps[0]) if eps else {}
    title, author_hint = split_title(work.title, work.author)
    minutes = sum((e.duration_ms or 0) for e in eps) / 60000 or None
    m = match_work(con, title, r.get("author") or author_hint, r.get("narrator"), minutes)
    out = {"work_id": work.id, "title": title, "verdict": m.verdict, "link": None, "fields": {}}
    if not m.record:
        return out
    our_author = r.get("author") or work.author or ""
    if our_author and not _placeholder(our_author) and json.loads(m.record["authors"]) \
            and not _mentions(m.record["text"], our_author):
        # a real author the record never mentions: same title, different work
        out["verdict"] = "rejected_author"
        return out
    out["link"] = m.record["link"]
    current = {"author": r.get("author") or work.author or None, "narrator": r.get("narrator") or None,
               "recorded_year": None, "parts_total": str(work.expected_total) if work.expected_total else None}
    pub = r.get("publisher") or ""
    y = re.search(r"\b(19|20)\d{2}\b", pub)
    current["recorded_year"] = y.group(0) if y else None
    manual = _manual_fields(session, work, eps[0] if eps else None)
    for field, new in proposed_values(m.record).items():
        if not new:
            continue
        cur = current.get(field)
        if not cur:
            action = "fill"
        elif sorted(_n(cur).split()) == sorted(_n(new).split()):
            action = "same"          # "Kopriva Stepan" == "Štěpán Kopřiva"
        else:
            # only the user's own values are worth a question; scraped/air-year
            # values lose to the catalog
            action = "conflict" if field in manual else "replace"
        out["fields"][field] = {"current": cur, "proposed": new, "action": action}
    return out


_PLACEHOLDER = re.compile(r"redakce|tvurci skupina|dokument|\d|radio|rozhlas", re.I)


def _placeholder(author: str) -> bool:
    """Byline stand-ins, not writers ("Redakce Radia Junior", "Tvurci skupina…")."""
    return bool(_PLACEHOLDER.search(unidecode(author)))


def _mentions(text: str, name: str) -> bool:
    t = _n(text)
    return all(tok in t.split() for tok in _n(name).split())


def _manual_fields(session: Session, work: Work, first_ep) -> set[str]:
    rows = session.query(MetadataValue).filter(
        MetadataValue.origin == FieldOrigin.MANUAL,
        ((MetadataValue.entity_type == "work") & (MetadataValue.entity_id == work.id))
        | ((MetadataValue.entity_type == "episode") & (MetadataValue.entity_id == (first_ep.id if first_ep else -1)))
    ).all()
    fields = {r.field for r in rows}
    out = {f for f in ("author", "narrator") if f in fields}
    if "publisher" in fields or "year" in fields:
        out.add("recorded_year")
    if work.expected_source == "manual":
        out.add("parts_total")
    return out


APPLY_ACTIONS = {"fill", "replace"}   # conflicts are the user's (never applied)


def apply_plan(session: Session, work: Work, plan: dict) -> list[str]:
    """Write fill/replace values as ENRICHED with the record link as source.
    Returns the fields written. Conflicts are skipped by design."""
    from audiobiblio.core.provenance import record_value
    src = plan.get("link") or "mluvenypanacek"
    written = []
    for field, d in plan.get("fields", {}).items():
        if d["action"] not in APPLY_ACTIONS:
            continue
        value = d["proposed"]
        if field == "author":
            work.author = value
            record_value(session, "work", work.id, "author", value, FieldOrigin.ENRICHED, src)
        elif field == "narrator":
            for ep in work.episodes:
                record_value(session, "episode", ep.id, "narrator", value, FieldOrigin.ENRICHED, src)
        elif field == "recorded_year":
            station = work.series.program.station.code if work.series and work.series.program else "CRo"
            record_value(session, "work", work.id, "publisher", f"{station} {value}", FieldOrigin.ENRICHED, src)
        elif field == "parts_total":
            work.expected_total, work.expected_source = int(value), "panacek"
        written.append(field)
    return written
