"""auto_finalize — the librarian: finished books move to the curated shelf.

Spec: docs/superpowers/specs/2026-07-22-finalize-to-curated-design.md

Daily pass over all works:
  1. destination mapped for the work's program (normalized name)?
  2. every episode has COMPLETE audio (a GONE part = not finished)
  3. expected_total met, OR the newest part aired >= QUIET_DAYS ago —
     except when rAPI supplied the total: then it must be met
  4. book layout requires author + narrator — otherwise the work is
     reported as "waiting for metadata" (never a half-named folder)
  5. finalize into the curated destination; a `final_path` provenance
     record marks the work as shelved (badge + idempotence)
"""
from __future__ import annotations

import re

from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import text as _sql_text
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
    # Literature wave 2 (2026-09-22): the user's absolute priority is audio
    # literature — every clearly-book program shelves as {Autor} [audio]/…
    # The author+narrator gate keeps half-named books out of the library.
    "letni cteni": ("/media/fiction", "book"),
    "pokracovani za pet minut": ("/media/fiction", "book"),
    "vltavske specialy": ("/media/fiction", "book"),
    "povidka": ("/media/fiction", "book"),
    "povidka na dvojce": ("/media/fiction", "book"),
    "povidka na sobotu": ("/media/fiction", "book"),
    "povidky klasiku": ("/media/fiction", "book"),
    "historicke povidky": ("/media/fiction", "book"),
    "hororove povidky": ("/media/fiction", "book"),
    "hra na sobotu": ("/media/fiction", "book"),
    "hra pro pametniky": ("/media/fiction", "book"),
    "rozhlasova hra": ("/media/fiction", "book"),
    "svatecni cteni": ("/media/fiction", "book"),
    # Čtenářský deník (CRo3): school-canon books read in full (user approved
    # 2026-10-09 with Jirásek: Staré pověsti české)
    "ctenarsky denik": ("/media/fiction", "book"),
    # Osudy is a PROGRAM, not a book slot (user 2026-09-26): each subject is
    # a multi-part episode — needs a per-subject subfolder collection layout,
    # NOT author dirs in fiction. Unmapped until that layout + target dir
    # are agreed; works stay safely queued meanwhile.
    "historie ceskeho zlocinu": ("/media/nonfiction/history [audio]", "collection"),
    "stopy, fakta, tajemstvi": ("/media/nonfiction/history [audio]", "collection"),
}

# Programs whose structured author scraping has proven reliable since July —
# the byline trust gate does not apply there.
TRUSTED_AUTHOR_PROGRAMS = {
    "cetba na pokracovani", "cetba s hvezdickou", "radiokniha",
    "hra na nedeli", "poctenicko",
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


def _resolved_origin(session: Session, entity: str, entity_id: int, field: str) -> str | None:
    """Origin name (MANUAL/ENRICHED/FILE/SCRAPED) of the winning value."""
    from audiobiblio.core.provenance import resolve_field
    rows = session.query(MetadataValue).filter_by(
        entity_type=entity, entity_id=entity_id, field=field).all()
    winner = resolve_field(rows)
    if winner is None:
        return None
    origin = getattr(winner, "origin", None)
    return getattr(origin, "name", None) or (str(origin) if origin else None)


def _title_key(title: str, author: str | None) -> str:
    from .finalize import _tokens, clean_book_title
    a = _tokens(author or "")
    t = re.sub(r"\([^)]*\)", " ", title or "")
    tok = _tokens(t)
    if a and tok[:len(a)] == a:              # old 'Autor - Autor Titul…' names
        t = " ".join(t.split()[len(a):])
    return " ".join(_tokens(clean_book_title(t, author)))


def same_title_on_shelf(work: Work, dest: Path) -> Path | None:
    """A folder in the author's shelf holding the same title in any naming
    style (user's 'Prijmeni Jmeno - Titul (Ctenar)(1h47m)', '(1853) Titul',
    older 'Autor - Autor Titul. Upoutavka (cte…)') — a curated copy exists."""
    key = _title_key(work.title, work.author)
    if not key or not dest.parent.is_dir():
        return None
    for d in dest.parent.iterdir():
        if d == dest or not d.is_dir() or d.name.startswith("@"):
            continue
        rest = d.name.split(" - ", 1)[1] if " - " in d.name else d.name
        if _title_key(rest, work.author) == key:
            return d
    return None


def _recording_year(session: Session, work: Work, first: Episode) -> int | None:
    """Suffix year = ROK NATOCENI (user rule) — carried by publisher
    ("CRo 2018" from "Natoceno v roce"); the broadcast year is only the
    fallback when no recording year is known anywhere."""
    import re as _re
    pub = _resolved_value(session, "work", work.id, "publisher") or ""
    m = _re.search(r"\b(1[89]\d{2}|20\d{2})\b", pub)
    if m:
        return int(m.group(1))
    return first.published_at.year if first.published_at else None


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
        dest = derive_curated_book_dir(work, first, Path(root), narrator, channel,
                                       rec_year=_recording_year(session, work, first))
        if dest is None:
            return None, "chybi autor/interpret (kniha nesmi na polici s polovicnim nazvem)"
        return dest, None
    label = f"{program.name} ({channel})" if channel else program.name
    return derive_curated_collection_dir(Path(root), label), None


def run_auto_finalize(session: Session, dry_run: bool = False,
                      now=None, verdicts: list[dict] | None = None) -> list[str]:
    """One librarian pass. Returns a human-readable action log; when
    `verdicts` is given, each per-work decision is also appended to it as
    {kind, work_id, detail, dest} (the shelf-queue page shows these)."""
    now = now or utcnow()
    report: list[str] = []

    def note(kind: str, work: Work, detail: str, dest: Path | None = None) -> None:
        report.append(f"{kind}: {work.title!r} (work #{work.id}) — {detail}"
                      if kind != "SHELVE" else f"SHELVE: {work.title!r} -> {dest}")
        if verdicts is not None:
            verdicts.append({"kind": kind, "work_id": work.id, "detail": detail,
                             "dest": str(dest) if dest else None})

    # Candidate prefilter in SQL. Loading every Work with episodes+assets
    # joinedloaded pulled the whole DB (113k works / 140k episodes) into ORM
    # objects — an OOM bomb that killed the container each nightly run once
    # the index grew (found 2026-09-06; shelving had been dead since 07-26).
    dest_prog_ids = [
        pid for (pid, pname) in session.query(Program.id, Program.name)
        if _norm(pname) in DESTINATIONS
    ]
    if not dest_prog_ids:
        return report
    pid_list = ",".join(str(i) for i in dest_prog_ids)
    candidate_ids = [wid for (wid,) in session.execute(_sql_text(f"""
        SELECT w.id FROM works w
        JOIN series se ON w.series_id = se.id
        WHERE se.program_id IN ({pid_list})
          AND EXISTS (SELECT 1 FROM episodes e WHERE e.work_id = w.id)
          AND NOT EXISTS (SELECT 1 FROM metadata_values mv
                          WHERE mv.entity_type = 'work' AND mv.entity_id = w.id
                            AND mv.field = 'final_path')
          AND NOT EXISTS (
              SELECT 1 FROM episodes e WHERE e.work_id = w.id
                AND NOT EXISTS (SELECT 1 FROM assets a
                                WHERE a.episode_id = e.id
                                  AND a.type = 'AUDIO' AND a.status = 'COMPLETE'
                                  AND a.file_path IS NOT NULL))
          AND NOT EXISTS (SELECT 1 FROM episodes e2 WHERE e2.work_id = w.id
                          AND e2.availability_status = 'GONE')
    """))]
    if not candidate_ids:
        return report
    works = (
        session.query(Work)
        .options(
            joinedload(Work.series).joinedload(Series.program).joinedload(Program.station),
            joinedload(Work.episodes).joinedload(Episode.assets),
        )
        .filter(Work.id.in_(candidate_ids))
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

        if (work.expected_source == "rapi" and work.expected_total
                and len(eps) < work.expected_total):
            # rAPI knows the real length — parts still to air, never shelve
            # a half book on the quiet-days fallback
            note("WAITING-PARTS", work,
                 f"{len(eps)}/{work.expected_total} dílů, čeká na další díly")
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
            # Author trust gate: byline-scraped authors are often the show's
            # redaktor, not the writer (live: 'povídky Fráni Šrámka' carried
            # author 'Petra Hynčíková'). A book goes to the author shelf only
            # when the title itself confirms the author ('Autor: Titul' or
            # the author's surname appearing in the title); otherwise it
            # waits for the metadata-control step.
            from unidecode import unidecode as _udx
            author_ok = _norm(program.name) in TRUSTED_AUTHOR_PROGRAMS
            if not author_ok and work.author:
                a_norm = _udx(work.author).lower().strip()
                t_norm = _udx(work.title or "").lower()
                surname = a_norm.split()[-1] if a_norm.split() else ""
                origin = _resolved_origin(session, "work", work.id, "author")
                author_ok = (
                    t_norm.startswith(a_norm)
                    or (surname and len(surname) > 3 and surname in t_norm)
                    or origin in ("MANUAL", "ENRICHED")
                )
            if work.author and not author_ok:
                note("AUTHOR-CHECK", work,
                     f"autor {work.author!r} nepotvrzen titulem, čeká na kontrolu metadat")
                continue
            narrator = _resolved_value(session, "episode", first.id, "narrator")
            # same naming as curated_destination (the web button) — the
            # nightly run used the broadcast year ('CRo 2026', 2026-10-10)
            dest = derive_curated_book_dir(work, first, Path(root), narrator, channel,
                                           rec_year=_recording_year(session, work, first))
            if dest is None:
                note("WAITING-METADATA", work, "chybí autor/interpret, kniha čeká")
                continue
        else:
            label = f"{program.name} ({channel})" if channel else program.name
            dest = derive_curated_collection_dir(Path(root), label)

        try:
            dest_occupied = dest.is_dir() and any(dest.iterdir())
        except OSError as ose:
            note("BAD-DEST", work, str(ose))
            continue
        if layout == "book" and dest_occupied:
            # One-way-door guard: a non-empty shelf dir this work does not
            # own means a copy is already curated (manual or another work).
            # Blind finalize would fill it with "-2" suffixed duplicates —
            # leave the arbitration (adopt vs. delete staging) to the user.
            note("COLLISION", work,
                 f"cíl {dest} už existuje a není prázdný; čeká na ruční rozhodnutí", dest)
            continue

        twin = same_title_on_shelf(work, dest) if layout == "book" else None
        if twin is not None:
            note("SAME-TITLE", work,
                 f"na polici už je {twin.name!r}; čeká na rozhodnutí (duplikát / jiná verze)", twin)
            continue

        note("SHELVE", work, "", dest)
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
            from audiobiblio.library.abs_metadata import publish_to_abs
            publish_to_abs(session, work, dest)
            log.info("auto_finalized", work_id=work.id, dest=str(dest))
        elif r.errors:
            log.warning("auto_finalize_errors", work_id=work.id,
                        errors=r.errors[:3])
    return report


def librarian_queue(session: Session, now=None) -> list[dict]:
    """What tonight's librarian would do with every finished book, as rows
    for the shelf-queue page — a dry run, nothing moves. Largest first."""
    verdicts: list[dict] = []
    run_auto_finalize(session, dry_run=True, now=now, verdicts=verdicts)
    rows = []
    for v in verdicts:
        w = session.get(Work, v["work_id"])
        eps = sorted(w.episodes, key=lambda e: (e.episode_number or 0, e.id))
        rows.append({
            **v, "title": w.title, "author": w.author,
            "narrator": _resolved_value(session, "episode", eps[0].id, "narrator") if eps else None,
            "parts": len(eps), "total": w.expected_total,
            "minutes": round(sum(e.duration_ms or 0 for e in eps) / 60000),
        })
    rows.sort(key=lambda r: -r["minutes"])
    return rows
