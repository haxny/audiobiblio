"""auto_finalize — the librarian: finished books move to the curated shelf.

Spec: docs/superpowers/specs/2026-07-22-finalize-to-curated-design.md

Daily pass over all works:
  1. destination mapped for the work's program (normalized name)?
  2. every episode has COMPLETE audio (a GONE part = not finished)
  3. expected_total met, OR the newest part aired >= QUIET_DAYS ago
  4. book layout requires author + narrator — otherwise the work is
     reported as "waiting for metadata" (never a half-named folder)
  5. finalize into the curated destination; a `final_path` provenance
     record marks the work as shelved (badge + idempotence)
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy.orm import Session, joinedload

from audiobiblio.core.time import utcnow
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, AvailabilityStatus, Episode, FieldOrigin,
    MetadataValue, Program, Series, Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.library.pipelines.finalize import (
    derive_curated_book_dir, derive_curated_collection_dir, finalize_work,
)

log = structlog.get_logger()

QUIET_DAYS = 14

# normalized program name -> (destination root inside the container, layout)
DESTINATIONS: dict[str, tuple[str, str]] = {
    "cetba na pokracovani": ("/media/fiction", "book"),
    "cetba s hvezdickou": ("/media/fiction", "book"),
    "radiokniha": ("/media/fiction", "book"),
    "hra na nedeli": ("/media/fiction", "book"),
    "poctenicko": ("/media/fiction", "book"),
    "historie ceskeho zlocinu": ("/media/nonfiction/history [audio]", "collection"),
    "stopy, fakta, tajemstvi": ("/media/nonfiction/history [audio]", "collection"),
}


def _norm(name: str) -> str:
    from unidecode import unidecode
    return unidecode(name or "").lower().rstrip(" .…")


def _resolved_value(session: Session, entity: str, entity_id: int, field: str) -> str | None:
    from audiobiblio.core.provenance import resolve_field
    rows = session.query(MetadataValue).filter_by(
        entity_type=entity, entity_id=entity_id, field=field).all()
    winner = resolve_field(rows)
    return winner.value if winner else None


def curated_destination(session: Session, work: Work) -> tuple[Path | None, str | None]:
    """Curated-shelf destination for a work, or (None, reason).

    Shared by the librarian (run_auto_finalize) and the explicit web
    Finalizovat button — both must aim at the SAME shelf (eBOOKs.fiction /
    eBOOKs.nonfiction), never at the working library.
    """
    program = work.series.program if work.series else None
    if program is None:
        return None, "dilo nema porad"
    dest_cfg = DESTINATIONS.get(_norm(program.name))
    if dest_cfg is None:
        return None, f"porad {program.name!r} nema kuratorskou mapu"
    root, layout = dest_cfg
    eps = sorted(work.episodes, key=lambda e: (e.episode_number or 0, e.id))
    if not eps:
        return None, "dilo nema epizody"
    first = eps[0]
    # User rule 2026-07-24: no channel digits in names — plain "CRo"
    channel = "CRo" if program.station else None
    if layout == "book":
        narrator = _resolved_value(session, "episode", first.id, "narrator")
        dest = derive_curated_book_dir(work, first, Path(root), narrator, channel)
        if dest is None:
            return None, "chybi autor/interpret (kniha nesmi na polici s polovicnim nazvem)"
        return dest, None
    label = f"{program.name} ({channel})" if channel else program.name
    return derive_curated_collection_dir(Path(root), label), None


def run_auto_finalize(session: Session, dry_run: bool = False,
                      now=None) -> list[str]:
    """One librarian pass. Returns a human-readable action log."""
    now = now or utcnow()
    report: list[str] = []

    works = (
        session.query(Work)
        .options(
            joinedload(Work.series).joinedload(Series.program).joinedload(Program.station),
            joinedload(Work.episodes).joinedload(Episode.assets),
        )
        .all()
    )
    for work in works:
        program = work.series.program if work.series else None
        if program is None:
            continue
        dest_cfg = DESTINATIONS.get(_norm(program.name))
        if dest_cfg is None:
            continue
        if _resolved_value(session, "work", work.id, "final_path"):
            continue  # already shelved
        eps = work.episodes
        if not eps:
            continue

        complete = all(
            any(a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
                and a.file_path for a in e.assets)
            for e in eps
        )
        if not complete:
            continue
        if any(e.availability_status == AvailabilityStatus.GONE for e in eps):
            continue

        if work.expected_total and len(eps) >= work.expected_total:
            aged = True
        else:
            newest = max((e.published_at for e in eps if e.published_at),
                         default=None)
            aged = newest is not None and newest <= now - timedelta(days=QUIET_DAYS)
        if not aged:
            continue

        root, layout = dest_cfg
        first = sorted(eps, key=lambda e: (e.episode_number or 0, e.id))[0]
        channel = "CRo" if program.station else None

        if layout == "book":
            narrator = _resolved_value(session, "episode", first.id, "narrator")
            dest = derive_curated_book_dir(work, first, Path(root), narrator, channel)
            if dest is None:
                report.append(
                    f"WAITING-METADATA: {work.title!r} (work #{work.id}) — "
                    f"chybí autor/interpret, kniha čeká")
                continue
        else:
            label = f"{program.name} ({channel})" if channel else program.name
            dest = derive_curated_collection_dir(Path(root), label)

        report.append(f"SHELVE: {work.title!r} -> {dest}")
        if dry_run:
            continue
        import re as _re
        book_stem = (_re.sub(r"\s*\(cte .*\)$", "", dest.name)
                     if layout == "book" else None)
        r = finalize_work(session, work, Path("/media/audiobooks"),
                          dry_run=False, dest_dir_override=dest,
                          book_stem=book_stem)
        report.append(f"  moved={r.moved} errors={len(r.errors)}")
        if r.moved and not r.errors:
            record_value(session, "work", work.id, "final_path", str(dest),
                         FieldOrigin.SCRAPED, "auto_finalize")
            session.commit()
            log.info("auto_finalized", work_id=work.id, dest=str(dest))
        elif r.errors:
            log.warning("auto_finalize_errors", work_id=work.id,
                        errors=r.errors[:3])
    return report
