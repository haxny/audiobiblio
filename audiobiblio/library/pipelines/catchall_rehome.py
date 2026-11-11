"""Move episodes out of the catch-all program "mujrozhlas" (id 104) to
their real programs — one work per book in book programs.

Live 2026-11-11: 845 works / 10,404 episodes sat in the catch-all; Otec
Goriot shared one work with eight other Radioseriál books and could never
be shelved. New items are routed at ingest (sources.program_match); this
moves the history. Nothing is deleted, files on disk stay put.

Two phases, because the app keeps writing to the same SQLite file: `plan`
only reads (the dry run), `apply` writes in small committed batches.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import AssetStatus, AssetType, Episode, Program, Series, Work
from audiobiblio.library.book_meta import parse_book_title
from audiobiblio.library.pipelines.auto_finalize import DESTINATIONS
from audiobiblio.library.pipelines.program_resolve import (
    book_key, is_catch_all, norm_name, resolve_program, show_url,
)

BATCH = 200


@dataclass(frozen=True)
class NewWork:
    """A work the apply phase creates (or finds, if it appeared meanwhile)."""
    program_id: int
    series_name: str
    title: str
    author: str | None


@dataclass
class Plan:
    moves: list[tuple[int, int | NewWork]] = field(default_factory=list)  # (episode id, target)
    stats: Counter = field(default_factory=Counter)
    per_program: Counter = field(default_factory=Counter)
    unresolved: Counter = field(default_factory=Counter)


def _has_audio(ep) -> bool:
    return any(a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
               and a.file_path for a in ep.assets)


class _Books:
    """Book identity -> target per program, loaded once per program."""

    def __init__(self, s: Session):
        self.s = s
        self.index: dict[int, list[tuple[tuple[str, str], int | NewWork]]] = {}
        # target -> {part number with audio} / {ext ids}
        self.parts: dict[int | NewWork, set[int]] = {}
        self.exts: dict[int | NewWork, set[str]] = {}

    def _load(self, program_id: int) -> list:
        if program_id not in self.index:
            rows = []
            works = (self.s.query(Work).join(Series)
                     .filter(Series.program_id == program_id).all())
            for w in works:
                k = book_key(w.title)
                if k:
                    rows.append((k, w.id))
                self._remember(w.id, w.episodes)
            self.index[program_id] = rows
        return self.index[program_id]

    def _remember(self, target, eps) -> None:
        self.parts.setdefault(target, set()).update(
            e.episode_number for e in eps if (e.episode_number or 0) > 0 and _has_audio(e))
        self.exts.setdefault(target, set()).update(e.ext_id for e in eps if e.ext_id)

    def target(self, program: Program, raw_title: str) -> int | NewWork | None:
        key = book_key(raw_title)
        if key is None:
            return None
        author, title = key
        for (a, t), tgt in self._load(program.id):
            if t == title and (not a or not author or a.split()[-1] == author.split()[-1]):
                return tgt
        meta = parse_book_title(raw_title)
        new = NewWork(program.id, program.name,
                      f"{meta.author}: {meta.title}" if meta.author else meta.title, meta.author)
        self.index[program.id].append((key, new))
        return new

    def show_target(self, program: Program, work: Work) -> int | NewWork:
        ser = self.s.query(Series).filter_by(program_id=program.id, name=work.series.name).first()
        existing = ser and self.s.query(Work).filter_by(series_id=ser.id, title=work.title).first()
        if existing:
            if existing.id not in self.parts:
                self._remember(existing.id, existing.episodes)
            return existing.id
        return NewWork(program.id, work.series.name, work.title, work.author)

    def is_duplicate(self, target, ep, by_part: bool) -> bool:
        if ep.ext_id and ep.ext_id in self.exts.get(target, ()):
            return True
        return by_part and _has_audio(ep) and (ep.episode_number or 0) > 0 \
            and ep.episode_number in self.parts.get(target, ())

    def claim(self, target, ep) -> None:
        self._remember(target, [ep])


def plan(s: Session, catch_all: Program) -> Plan:
    """Read-only: where every catch-all episode goes."""
    out, books, programs = Plan(), _Books(s), {}
    works = s.query(Work).join(Series).filter(Series.program_id == catch_all.id).all()
    for w in works:
        for ep in w.episodes:
            key = (show_url(ep.url), w.series.name, w.title)
            if key not in programs:
                programs[key] = resolve_program(s, ep.url, (w.series.name, w.title))
            prog = programs[key]
            if prog is None or is_catch_all(prog):
                out.unresolved[w.title] += 1
                out.stats["unresolved"] += 1
                continue
            book = DESTINATIONS.get(norm_name(prog.name), (None, None))[1] == "book"
            target = books.target(prog, ep.title or "") if book else books.show_target(prog, w)
            if target is None:
                out.unresolved[w.title] += 1
                out.stats["unresolved"] += 1
                continue
            if books.is_duplicate(target, ep, by_part=book):
                out.stats["duplicate_left"] += 1
                continue
            books.claim(target, ep)
            out.moves.append((ep.id, target))
            out.per_program[f"{prog.name} ({prog.station.code}) #{prog.id}"] += 1
    out.stats["episodes_to_move"] = len(out.moves)
    out.stats["works_to_create"] = len({t for _, t in out.moves if isinstance(t, NewWork)})
    return out


def _work_for(s: Session, target: int | NewWork, created: dict) -> int:
    if isinstance(target, int):
        return target
    if target not in created:
        ser = s.query(Series).filter_by(program_id=target.program_id, name=target.series_name).first()
        if ser is None:
            ser = Series(program_id=target.program_id, name=target.series_name)
            s.add(ser)
            s.flush()
        w = s.query(Work).filter_by(series_id=ser.id, title=target.title).first()
        if w is None:
            w = Work(series_id=ser.id, title=target.title, author=target.author)
            s.add(w)
            s.flush()
        created[target] = w.id
    return created[target]


def apply(s: Session, p: Plan, batch: int = BATCH, retries: int = 5) -> int:
    """Write the plan in committed batches; returns episodes moved."""
    created: dict[NewWork, int] = {}
    moved = 0
    for i in range(0, len(p.moves), batch):
        chunk = p.moves[i:i + batch]
        for attempt in range(retries):
            try:
                for ep_id, target in chunk:
                    s.get(Episode, ep_id).work_id = _work_for(s, target, created)
                s.commit()
                break
            except OperationalError:
                s.rollback()
                created.clear()  # ids from the rolled-back batch are void
                if attempt == retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))
        moved += len(chunk)
    return moved
