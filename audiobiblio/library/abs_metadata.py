"""metadata.json for Audiobookshelf.

ABS reads the narrator only from the `composer` tag — but composer is the
author of the music (user rule, 2026-10-09). ABS also reads a metadata.json
in the item folder and our libraries rank it first (absMetadata precedence),
so this is the clean channel: the file tags stay as the user wants them and
ABS gets title/authors/narrators/genres/year/publisher/description from the
audiobiblio DB (resolved provenance, manual edits win).

The file is MERGED: only keys we have a value for are written; everything
else in an existing metadata.json (chapters, tags, ABS-own keys) is kept.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import MetadataValue, Work
from audiobiblio.core.provenance import resolve_field
from audiobiblio.library.sync import compute_resolved

log = structlog.get_logger()

# container mount → ABS library name; the nightly scan job rescans libraries
# marked dirty here (ABS's own watcher misses folders we create).
_MOUNT_LIBRARY = {"/media/fiction/": "Fiction", "/media/nonfiction/": "Nonfiction [audio]",
                  "/media/ebooks/4kids/": "Kids"}
DIRTY_FILE = "abs_dirty_libraries.json"

_NAME_SPLIT = re.compile(r"\s*(?:,|;|&|\s+a\s+)\s*")
_OTHERS = {"dalsi", "další", "a dalsi", "a další"}


def split_names(value: str | None) -> list[str]:
    """'Dana Syslova, Antonie Baresova a dalsi' → ['Dana Syslova', 'Antonie Baresova']."""
    if not value:
        return []
    return [n for n in (p.strip() for p in _NAME_SPLIT.split(value))
            if n and n.lower() not in _OTHERS]


def build_abs_metadata(session: Session, work: Work) -> dict:
    """ABS book metadata from the work's resolved values (first episode for
    episode-level fields)."""
    eps = sorted(work.episodes, key=lambda e: (e.episode_number or 0, e.id))
    r = compute_resolved(session, eps[0]) if eps else {}
    title_rows = session.query(MetadataValue).filter_by(
        entity_type="work", entity_id=work.id, field="title").all()
    winner = resolve_field(title_rows)
    title = (winner.value if winner is not None and winner.origin.value == "manual" else None) \
        or work.title
    md = {
        "title": title,
        "authors": split_names(r.get("author") or work.author),
        "narrators": split_names(r.get("narrator")),
        "genres": [g.strip() for g in (r.get("genre") or "").split(";") if g.strip()],
        "publishedYear": str(work.year) if work.year else (r.get("year") or None),
        "publisher": r.get("publisher") or None,
        "description": r.get("description") or None,
    }
    return {k: v for k, v in md.items() if v}


def write_abs_metadata(folder: Path, data: dict) -> bool:
    """Merge `data` into folder/metadata.json. Returns True when the file changed."""
    path = Path(folder) / "metadata.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    merged = {**current, **{k: v for k, v in data.items() if v not in (None, "", [])}}
    if merged == current and path.exists():
        return False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def mark_library_dirty(folder: Path) -> str | None:
    """Remember which ABS library needs a rescan for this folder."""
    from audiobiblio.core.json_state import load_json, save_json
    lib = next((name for mount, name in _MOUNT_LIBRARY.items()
                if str(folder).startswith(mount)), None)
    if lib:
        state = load_json(DIRTY_FILE)
        state[lib] = True
        save_json(DIRTY_FILE, state)
    return lib


def publish_to_abs(session: Session, work: Work, folder: Path) -> None:
    """After shelving: metadata.json for ABS + mark its library for rescan.
    Best effort — never fails the shelving itself."""
    try:
        changed = write_abs_metadata(Path(folder), build_abs_metadata(session, work))
        lib = mark_library_dirty(Path(folder))
        log.info("abs_metadata_written", work_id=work.id, changed=changed, library=lib)
    except Exception:
        log.warning("abs_metadata_failed", work_id=work.id, exc_info=True)
