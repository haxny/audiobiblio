"""Identity of a multi-part book across ingest paths.

Four paths create works for rAPI serial parts (HTML crawler, show-sweep,
serial-sweep, rAPI crawl) and each used its own title rule, so every title
variant split a book in two (live 2026-11: Hrabal, Prosecké peklo — parts
waiting forever in the other half).

Rule, shared by all paths:
  1. a work stamped with this serial UUID is the book;
  2. else an unstamped work of the same program with the same book key
     (author + title head, subtitle ignored) is the book — and gets stamped;
  3. a work stamped with ANOTHER serial UUID is never reused: that is an
     earlier broadcast of the same title (re-air), its own book.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    AssetStatus, AssetType, Episode, MetadataValue, Series, Work,
)
from audiobiblio.library.pipelines.program_resolve import _same_author, book_key


def serial_uuid_of(e: dict) -> str | None:
    """Serial UUID of an rAPI episode object (relationships.serial.data.id)."""
    data = (((e.get("relationships") or {}).get("serial") or {}).get("data") or {})
    return data.get("id") or None


def stamp(work: Work, serial_uuid: str | None) -> None:
    if serial_uuid and not work.serial_uuid:
        work.serial_uuid = serial_uuid


def resolve_serial_work(session: Session, program_id: int,
                        serial_uuid: str | None, serial_title: str | None) -> Work | None:
    """The existing work of this serial, or None (caller creates it with
    `serial_uuid=` so the next path finds it by UUID)."""
    if serial_uuid:
        stamped = session.query(Work).filter(Work.serial_uuid == serial_uuid).first()
        if stamped is not None:
            return stamped
    key = book_key(serial_title)
    if key is None:
        return None
    author, title = key
    rows = session.execute(
        select(Work.id, Work.title, Work.serial_uuid).join(Series, Work.series_id == Series.id)
        .where(Series.program_id == program_id).order_by(Work.id)).all()
    for wid, wtitle, wuuid in rows:
        if serial_uuid and wuuid and wuuid != serial_uuid:
            continue  # another broadcast of the same title
        wk = book_key(wtitle)
        if wk and wk[1] == title and _same_author(wk[0], author):
            work = session.get(Work, wid)
            stamp(work, serial_uuid)
            return work
    return None


# --- finding and repairing books that are already split ----------------------

@dataclass(frozen=True)
class SplitGroup:
    works: tuple[Work, ...]
    canonical: Work
    duplicates: tuple[Work, ...]


def _parts(work: Work) -> set[int]:
    return {e.episode_number for e in work.episodes if e.episode_number is not None}


def find_split_groups(session: Session, program_id: int | None = None) -> list[SplitGroup]:
    """Works of one program that are the same book AND complete each other:
    merging them yields parts neither has alone. Complete re-air copies and
    works of different serial UUIDs are not splits."""
    q = session.query(Work).join(Series, Work.series_id == Series.id)
    if program_id is not None:
        q = q.filter(Series.program_id == program_id)
    by_program: dict[int, list[Work]] = {}
    for w in q.order_by(Work.id).all():
        by_program.setdefault(w.series.program_id, []).append(w)

    out: list[SplitGroup] = []
    for works in by_program.values():
        groups: list[tuple[str, str, list[Work]]] = []
        for w in works:
            key = book_key(w.title)
            if key is None:
                continue
            for gauthor, gtitle, members in groups:
                if gtitle == key[1] and _same_author(gauthor, key[0]):
                    members.append(w)
                    break
            else:
                groups.append((key[0], key[1], [w]))
        for _a, _t, members in groups:
            uuids = {m.serial_uuid for m in members if m.serial_uuid}
            if len(members) < 2 or len(uuids) > 1:
                continue
            union = set().union(*(_parts(m) for m in members))
            if len(union) <= max(len(_parts(m)) for m in members):
                continue
            ordered = sorted(members, key=lambda m: (-len(_parts(m)),
                                                     0 if m.serial_uuid else 1, m.id))
            out.append(SplitGroup(tuple(ordered), ordered[0], tuple(ordered[1:])))
    return out


def _has_audio(ep: Episode) -> bool:
    return any(a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
               and a.file_path for a in ep.assets)


def merge_works(session: Session, canonical_id: int, duplicate_id: int,
                library_dir: Path, dry_run: bool = True,
                trash_fn: Callable[[Path], Path] | None = None) -> list[str]:
    """Move every part of *duplicate_id* into *canonical_id* and delete the
    emptied work. A part number both works hold is merged episode-to-episode
    (the copy with audio survives; two copies with audio are left for a human).
    The caller commits. dry_run touches nothing."""
    from audiobiblio.dedupe.clusters import ManualMetadataProtectionError, merge_episodes
    from audiobiblio.core.db.models import FieldOrigin

    if canonical_id == duplicate_id:
        raise ValueError("canonical and duplicate must differ")
    canonical, duplicate = session.get(Work, canonical_id), session.get(Work, duplicate_id)
    if canonical is None or duplicate is None:
        raise ValueError("work not found")
    if not dry_run and trash_fn is None:
        raise ValueError("trash_fn must be provided when dry_run=False")
    if session.query(MetadataValue).filter(
            MetadataValue.entity_type == "work", MetadataValue.entity_id == duplicate_id,
            MetadataValue.origin == FieldOrigin.MANUAL).count():
        raise ManualMetadataProtectionError(
            f"Work {duplicate_id} has MANUAL metadata; merge refused to protect curated data.")

    actions: list[str] = []
    by_number = {e.episode_number: e for e in canonical.episodes if e.episode_number is not None}
    for ep in list(duplicate.episodes):
        twin = by_number.get(ep.episode_number) if ep.episode_number is not None else None
        if twin is None:
            actions.append(f"move episode {ep.id} (part {ep.episode_number}) "
                           f"from work {duplicate_id} to {canonical_id}")
            if not dry_run:
                ep.work_id = canonical_id
            continue
        if _has_audio(twin) and _has_audio(ep):
            actions.append(f"CONFLICT part {ep.episode_number}: episodes {twin.id} and {ep.id} "
                           "both have audio — left in place for a human")
            continue
        keep, drop = (ep, twin) if _has_audio(ep) else (twin, ep)
        actions.append(f"merge episode {drop.id} into {keep.id} (part {ep.episode_number})")
        actions += merge_episodes(session, keep.id, drop.id, library_dir,
                                  dry_run=dry_run, trash_fn=trash_fn)
        if not dry_run and keep.work_id != canonical_id:
            keep.work_id = canonical_id
    if duplicate.expected_total and not (canonical.expected_total or 0) >= duplicate.expected_total:
        actions.append(f"expected_total {canonical.expected_total} -> {duplicate.expected_total}")
    if duplicate.serial_uuid and not canonical.serial_uuid:
        actions.append(f"stamp serial_uuid {duplicate.serial_uuid}")
    actions.append(f"delete work {duplicate_id} ({duplicate.title[:60]!r})")
    if dry_run:
        return actions

    if duplicate.expected_total and not (canonical.expected_total or 0) >= duplicate.expected_total:
        canonical.expected_total = duplicate.expected_total
    stamp(canonical, duplicate.serial_uuid)
    session.flush()
    session.expire(canonical, ["episodes"])
    session.expire(duplicate, ["episodes"])
    if duplicate.episodes:
        actions.append(f"work {duplicate_id} kept: {len(duplicate.episodes)} episode(s) unresolved")
        return actions
    session.query(MetadataValue).filter(MetadataValue.entity_type == "work",
                                        MetadataValue.entity_id == duplicate_id
                                        ).delete(synchronize_session=False)
    session.delete(duplicate)
    session.flush()
    return actions
