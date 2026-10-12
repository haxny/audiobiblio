"""dup_review — temp-dir copies of books already on the curated shelf.

The system proposes, the user decides, the executor acts:

  propose  identical copy (same audio count, every file the same size within
           tag-edit tolerance) → RECYCLE_TEMP; anything else has no proposal
  decide   RECYCLE_TEMP  temp copy goes to #recycle, shelf stays
           REPLACE_SHELF shelf folder goes to #recycle, temp copy takes its place
           KEEP_BOTH     different version — remembered, never proposed again
  execute  re-verifies before every move; files are only ever moved into the
           share's #recycle (restorable in DSM), never deleted

A shelf folder audiobiblio manages (assets inside it) is never replaced from
here — that goes through the work page (Adoptovat).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Asset, DupCandidate
from audiobiblio.core.time import utcnow

BASE = Path("/media/ebooks")
RECYCLE = BASE / "#recycle" / "audiobiblio-dups"
AUDIO = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wma", ".aac", ".wav"}

RECYCLE_TEMP = "recycle_temp"
REPLACE_SHELF = "replace_shelf"
KEEP_BOTH = "keep_both"
DECISIONS = {RECYCLE_TEMP, REPLACE_SHELF, KEEP_BOTH}
TAG_TOLERANCE = 65536   # retagging changes a file by a few KB, never more


def audio_sizes(d: Path) -> list[int]:
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith("@")]
        out.extend(os.path.getsize(os.path.join(root, f)) for f in files
                   if Path(f).suffix.lower() in AUDIO)
    return sorted(out)


def same_audio(a: Path, b: Path) -> bool:
    sa, sb = audio_sizes(a), audio_sizes(b)
    return bool(sa) and len(sa) == len(sb) and all(
        abs(x - y) <= min(TAG_TOLERANCE, x * 0.01) for x, y in zip(sa, sb))


def _verdict(temp: Path, shelf: Path) -> str:
    if same_audio(temp, shelf):
        return "IDENTICAL"
    return "shelf_bigger" if sum(audio_sizes(shelf)) > sum(audio_sizes(temp)) else "temp_bigger"


def import_rows(session: Session, rows: list[dict]) -> int:
    """Upsert candidates (rows as in dups_copy_of_shelved.tsv). A decided or
    finished candidate keeps its decision — a re-scan never reopens it."""
    added = 0
    for r in rows:
        temp, shelf = Path(r["temp"]), Path(r["shelf"])
        if not temp.is_dir() or not shelf.is_dir():
            continue
        c = session.query(DupCandidate).filter_by(temp_path=str(temp)).first()
        if c is not None and (c.decision or c.status != "open"):
            continue
        verdict = _verdict(temp, shelf)
        fields = dict(
            shelf_path=str(shelf), mb_temp=float(r.get("MB_temp") or 0),
            files_temp=int(r.get("files_temp") or 0), mb_shelf=float(r.get("MB_shelf") or 0),
            files_shelf=int(r.get("files_shelf") or 0),
            minutes=float(r["min_temp"]) if r.get("min_temp") else None,
            verdict=verdict, proposal=RECYCLE_TEMP if verdict == "IDENTICAL" else None)
        if c is None:
            session.add(DupCandidate(temp_path=str(temp), status="open", **fields))
            added += 1
        else:
            for k, v in fields.items():
                setattr(c, k, v)
    session.commit()
    return added


def decide(session: Session, ids: list[int], decision: str | None) -> int:
    """Record (or clear, with None) the user's decision; nothing moves yet."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    n = 0
    for c in session.query(DupCandidate).filter(DupCandidate.id.in_(ids),
                                                DupCandidate.status.in_(["open", "decided", "error"])):
        c.decision = decision
        c.status = "decided" if decision else "open"
        c.decided_at = utcnow() if decision else None
        c.result = None
        n += 1
    session.commit()
    return n


def accept_proposals(session: Session, verdict: str = "IDENTICAL") -> int:
    """The user accepts every open proposal of one kind in one click."""
    ids = [c.id for c in session.query(DupCandidate).filter_by(
        status="open", verdict=verdict).filter(DupCandidate.proposal.isnot(None))]
    return decide(session, ids, RECYCLE_TEMP) if ids else 0


def _to_recycle(path: Path) -> Path:
    try:
        rel = path.relative_to(BASE)
    except ValueError:
        rel = Path(path.name)
    dest = RECYCLE / rel
    n = 2
    while dest.exists():
        dest = RECYCLE / rel.parent / f"{rel.name} ({n})"
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def _managed(session: Session, shelf: Path) -> bool:
    return session.query(Asset).filter(Asset.file_path.like(str(shelf) + "/%")).first() is not None


def _act(session: Session, c: DupCandidate) -> tuple[str, str]:
    temp, shelf = Path(c.temp_path), Path(c.shelf_path)
    if not temp.is_dir() or not shelf.is_dir():
        return "gone", "složka už neexistuje"
    if c.decision == KEEP_BOTH:
        return "done", "ponecháno obojí (jiná verze)"
    if c.decision == RECYCLE_TEMP:
        if c.verdict == "IDENTICAL" and not same_audio(temp, shelf):
            return "error", "kopie už nejsou shodné — rozhodněte znovu"
        return "done", f"kopie v koši: {_to_recycle(temp)}"
    if c.decision == REPLACE_SHELF:
        if _managed(session, shelf):
            return "error", "polici spravuje audiobiblio — nahraďte přes stránku díla"
        old = _to_recycle(shelf)
        shutil.move(str(temp), str(shelf))
        return "done", f"police nahrazena, původní v koši: {old}"
    return "error", f"neznámé rozhodnutí {c.decision!r}"


def execute(session: Session, limit: int = 1000) -> dict[str, int]:
    """Carry out decided candidates. Returns counts per outcome."""
    counts: dict[str, int] = {}
    for c in (session.query(DupCandidate).filter_by(status="decided")
              .order_by(DupCandidate.mb_temp.desc()).limit(limit).all()):
        try:
            status, result = _act(session, c)
        except OSError as e:
            status, result = "error", str(e)[:400]
        c.status, c.result, c.executed_at = status, result, utcnow()
        counts[status] = counts.get(status, 0) + 1
        session.commit()
    return counts
