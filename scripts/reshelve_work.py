"""Re-shelve a curated work whose folder was derived from wrong metadata.

2026-10-09: books landed under "_ [audio]" (author missing — the guard
tested the slug "_" instead of the raw value) or under the article byline
author with a sentence fragment as narrator. This sets the corrected
metadata as MANUAL, then moves the files with the regular finalize_work()
into the destination derived from it; leftovers of the old folder (_meta,
cover.jpg) follow, the empty old folder is removed, final_path is
re-recorded and the files are re-tagged.

Usage (in the container), dry run unless GO=1:
  WORK_ID=1103 META='{"author":"Vit Vencl","title":"Chvilka stesti",
                      "narrator":"Dana Syslova, Antonie Baresova a dalsi",
                      "publisher":"CRo2 2022","year":2022}' python scripts/reshelve_work.py
"""
import json
import os
import shutil
from pathlib import Path

from sqlalchemy.orm import joinedload

from audiobiblio.core.db.models import FieldOrigin, MetadataValue, Work
from audiobiblio.core.db.session import get_session
from audiobiblio.core.provenance import record_value
from audiobiblio.library.pipelines.auto_finalize import curated_destination
from audiobiblio.library.pipelines.finalize import finalize_work
from audiobiblio.library.pipelines.library import default_library_root
from audiobiblio.library.sync import sync_episode_tags

GO = os.environ.get("GO") == "1"
WORK_ID = int(os.environ["WORK_ID"])
META = json.loads(os.environ["META"])
SRC = "reshelve"


def _apply_metadata(s, work: Work) -> None:
    for field in ("author", "title", "year"):
        if field in META:
            setattr(work, field, META[field])
            record_value(s, "work", work.id, field, str(META[field]), FieldOrigin.MANUAL, SRC)
    if "publisher" in META:
        record_value(s, "work", work.id, "publisher", META["publisher"], FieldOrigin.MANUAL, SRC)
    if "narrator" in META:
        for ep in work.episodes:
            record_value(s, "episode", ep.id, "narrator", META["narrator"], FieldOrigin.MANUAL, SRC)


def _move_leftovers(old: Path, new: Path) -> list[str]:
    """Everything finalize_work did not take (sidecars in _meta, cover.jpg)."""
    moved = []
    for src in sorted(old.rglob("*")):
        if src.is_dir() or "@eaDir" in src.parts:
            continue
        dst = new / src.relative_to(old)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(str(src.relative_to(old)))
    return moved


def _remove_if_empty(path: Path) -> None:
    for d in sorted([p for p in path.rglob("*") if p.is_dir()], reverse=True):
        if d.name == "@eaDir":
            shutil.rmtree(d, ignore_errors=True)
        elif not any(d.iterdir()):
            d.rmdir()
    eadir = path / "@eaDir"
    if eadir.exists():
        shutil.rmtree(eadir, ignore_errors=True)
    if path.exists() and not any(path.iterdir()):
        path.rmdir()


def main() -> None:
    s = get_session()
    work = s.query(Work).options(joinedload(Work.episodes)).get(WORK_ID)
    fp_rows = s.query(MetadataValue).filter_by(entity_type="work", entity_id=WORK_ID,
                                                field="final_path").all()
    old = Path(fp_rows[0].value) if fp_rows else None
    print(f"work #{WORK_ID} {work.title!r} — old shelf: {old}")
    _apply_metadata(s, work)
    s.flush()
    dest, why = curated_destination(s, work)
    if dest is None:
        print(f"no destination: {why}")
        s.rollback()
        return
    print(f"new shelf: {dest}")
    import re
    stem = re.sub(r"\s*\(cte .*\)$", "", dest.name)
    report = finalize_work(s, work, default_library_root(), dry_run=not GO,
                           dest_dir_override=dest, book_stem=stem)
    for a in report.actions[:6]:
        print("  ", a)
    print(f"  … {len(report.actions)} actions, errors: {report.errors}")
    if not GO:
        s.rollback()
        print("dry run — GO=1 to apply")
        return
    blocking = [e for e in report.errors if not e.startswith("Missing on disk")]
    if blocking:
        s.rollback()
        print("ABORT: blocking errors")
        return
    for row in fp_rows:
        s.delete(row)
    record_value(s, "work", work.id, "final_path", str(dest), FieldOrigin.MANUAL, SRC)
    s.commit()
    if old and old.exists() and old != dest:
        print("leftovers moved:", _move_leftovers(old, dest))
        _remove_if_empty(old)
        if old.parent.exists() and not any(p for p in old.parent.iterdir() if p.name != "@eaDir"):
            _remove_if_empty(old.parent)
    for ep in work.episodes:
        sync_episode_tags(s, ep, write=True)
    s.commit()
    print("done:", dest)


if __name__ == "__main__":
    main()
