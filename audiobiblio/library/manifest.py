"""What audiobiblio last saw in a book folder, recorded next to the audio.

foobar / cmdr will never write our bookkeeping, so audiobiblio keeps its own
view in the folder's metadata.json under the key "audiobiblio": a stable guid
for the book, the serial UUID and, per audio file, name / size / mtime /
payload fingerprint / the tags we last saw. Any later difference between this
record and the disk is the signal to read the real files and update the DB
(reconcile comes later). This module only WRITES the record, merges it into
the file without touching the keys ABS or the user keep there, and rewrites
only when the content really changed (the timestamp is excluded from the
comparison, so ABS is not told to rescan every night).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import AssetStatus, AssetType, FieldOrigin, Work
from audiobiblio.core.provenance import record_value
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

MANIFEST_KEY = "audiobiblio"
SCHEMA = 1


def work_guid(session: Session, work: Work) -> str:
    """Stable identity of the book that survives rename, move and DB restore
    (it lives in the folder too). Created once, kept in the provenance store."""
    from audiobiblio.library.pipelines.auto_finalize import _resolved_value
    guid = _resolved_value(session, "work", work.id, "guid")
    if guid:
        return str(guid)
    guid = str(uuid.uuid4())
    record_value(session, "work", work.id, "guid", guid, FieldOrigin.ENRICHED, "manifest")
    session.flush()
    return guid


def build_manifest(session: Session, work: Work, folder: Path,
                   create_guid: bool = True) -> dict:
    """The record for *work* in *folder* (without the timestamp). With
    create_guid=False nothing is written to the DB (dry runs): a book without
    a guid yet shows "(new)"."""
    from audiobiblio.library import pathfollow
    folder = Path(folder)
    files = []
    for ep in sorted(work.episodes, key=lambda e: (e.episode_number or 0, e.id)):
        for a in ep.assets:
            if a.type != AssetType.AUDIO or a.status != AssetStatus.COMPLETE or not a.file_path:
                continue
            path = Path(a.file_path)
            if path.parent != folder or not path.is_file():
                continue
            st = os.stat(path)
            extra = a.extra or {}
            files.append({
                "name": path.name, "episode_id": ep.id, "part": ep.episode_number,
                "size": st.st_size, "mtime": int(st.st_mtime),
                "fp": extra.get("fp") or pathfollow.audio_fingerprint(path),
                "tags": extra.get("tag_snapshot") or {},
            })
    if create_guid:
        guid = work_guid(session, work)
    else:
        from audiobiblio.library.pipelines.auto_finalize import _resolved_value
        guid = _resolved_value(session, "work", work.id, "guid") or "(new)"
    return {"schema": SCHEMA, "guid": guid, "work_id": work.id,
            "serial_uuid": work.serial_uuid, "files": files}


def _current(folder: Path) -> dict:
    try:
        data = json.loads((Path(folder) / "metadata.json").read_text(encoding="utf-8"))
        return data.get(MANIFEST_KEY) or {} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_manifest(session: Session, work: Work, folder: Path) -> bool:
    """Record the folder's state in its metadata.json. True when the file changed.
    A folder shared by several works has no single book identity: skipped."""
    from audiobiblio.library.abs_metadata import folder_works, write_abs_metadata
    folder = Path(folder)
    if len(folder_works(session, folder)) > 1:
        return False
    new = build_manifest(session, work, folder)
    old = {k: v for k, v in _current(folder).items() if k != "written_at"}
    if old == new:
        return False
    return write_abs_metadata(folder, {MANIFEST_KEY: {**new, "written_at": utcnow().isoformat()}})


def needs_write(session: Session, work: Work, folder: Path) -> bool:
    """Dry-run twin of write_manifest: would the folder's record change?
    Never writes (neither the file nor a new guid)."""
    from audiobiblio.library.abs_metadata import folder_works
    folder = Path(folder)
    if len(folder_works(session, folder)) > 1:
        return False
    new = build_manifest(session, work, folder, create_guid=False)
    old = {k: v for k, v in _current(folder).items() if k != "written_at"}
    if new["guid"] == "(new)":
        return True
    return old != new
