"""Move episodes out of the catch-all program "mujrozhlas" (id 104) to
their real programs — one work per book in book programs.

Live 2026-11-11: 845 works / 10,404 episodes sat in the catch-all; Otec
Goriot shared one work with eight other Radioseriál books and could never
be shelved. New items are routed at ingest (sources.program_match); this
script moves the history. Nothing is deleted, files on disk stay put.

Run: scripts/catchall_rehome.py [write] (dry run by default).
"""
from __future__ import annotations

from collections import Counter

from audiobiblio.core.db.models import (
    AssetStatus, AssetType, Program, Series, Work,
)
from audiobiblio.core.db.session import get_session
from audiobiblio.library.book_meta import parse_book_title
from audiobiblio.library.pipelines.auto_finalize import DESTINATIONS
from audiobiblio.library.pipelines.program_resolve import (
    book_key, find_book_work, is_catch_all, norm_name, resolve_program,
)


def _has_audio(ep) -> bool:
    return any(a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
               and a.file_path for a in ep.assets)


def _series(s, program: Program, name: str) -> Series:
    ser = s.query(Series).filter_by(program_id=program.id, name=name).first()
    if ser is None:
        ser = Series(program_id=program.id, name=name)
        s.add(ser)
        s.flush()
    return ser


def _already_there(target: Work, ep, by_part: bool) -> bool:
    """The target work already has this part with audio (or this ext_id)."""
    for other in target.episodes:
        if other.id == ep.id:
            continue
        if ep.ext_id and other.ext_id == ep.ext_id:
            return True
        if by_part and (ep.episode_number or 0) > 0 \
                and other.episode_number == ep.episode_number and _has_audio(other):
            return True
    return False


def _book_work(s, program: Program, ep, stats) -> Work | None:
    raw = ep.title or ""
    if book_key(raw) is None:
        return None
    work = find_book_work(s, program, raw)
    if work is None:
        meta = parse_book_title(raw)
        title = f"{meta.author}: {meta.title}" if meta.author else meta.title
        work = Work(series_id=_series(s, program, program.name).id, title=title,
                    author=meta.author)
        s.add(work)
        s.flush()
        stats["book_works_created"] += 1
    return work


def _move(ep, target: Work, stats) -> None:
    ep.work_id = target.id
    target.episodes.append(ep)
    stats["episodes_moved"] += 1


def rehome(s, catch_all: Program, write: bool) -> tuple[Counter, Counter, Counter]:
    stats, per_program, unresolved = Counter(), Counter(), Counter()
    works = s.query(Work).join(Series).filter(Series.program_id == catch_all.id).all()
    for w in works:
        names = (w.series.name, w.title)
        for ep in list(w.episodes):
            prog = resolve_program(s, ep.url, names)
            if prog is None or is_catch_all(prog):
                unresolved[w.title] += 1
                stats["unresolved"] += 1
                continue
            book = DESTINATIONS.get(norm_name(prog.name), (None, None))[1] == "book"
            if book:
                target = _book_work(s, prog, ep, stats)
            else:
                ser = _series(s, prog, w.series.name)
                target = (s.query(Work).filter_by(series_id=ser.id, title=w.title).first()
                          or Work(series_id=ser.id, title=w.title, author=w.author))
                if target.id is None:
                    s.add(target)
                    s.flush()
                    stats["show_works_created"] += 1
            if target is None:
                unresolved[w.title] += 1
                stats["unresolved"] += 1
                continue
            if _already_there(target, ep, by_part=book):
                stats["duplicate_left"] += 1
                continue
            _move(ep, target, stats)
            per_program[f"{prog.name} ({prog.station.code}) #{prog.id}"] += 1
    if write:
        s.commit()
    else:
        s.rollback()
    return stats, per_program, unresolved
