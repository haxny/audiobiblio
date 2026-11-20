"""Follow the files: the disk is the truth, the DB must keep up.

The user renames folders and files, moves books between shelves and retags in
cmdr / foobar. Every audio asset therefore carries a fingerprint of its AUDIO
PAYLOAD (container tags excluded) that survives rename, move and retag. When
an asset's path is dead, the roots are scanned for a file with the same
fingerprint and the asset (and its sidecars, and the work's final_path) are
re-pointed. A unique match is applied by the machine; ambiguity is reported.

A folder the user renamed by hand also carries metadata ("Autor - (1989)
Titul (cte Narrator, CRo 1989)"): it is read back as MANUAL values.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import bindparam, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, FieldOrigin, Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

DEFAULT_ROOTS = ("/media/audiobooks", "/media/fiction", "/media/nonfiction")
AUDIO_SUFFIXES = {".m4a", ".m4b", ".mp4", ".mp3", ".flac", ".ogg", ".opus"}
_SKIP_DIRS = {"@eaDir", ".git", "#recycle"}
_CHUNK = 64 * 1024
_FP_KEY = "fp"
_HISTORY_KEY = "path_history"


# --- fingerprint ---------------------------------------------------------------

def _mp4_payload(f, size: int) -> tuple[int, int] | None:
    pos = 0
    while pos + 8 <= size:
        f.seek(pos)
        head = f.read(16)
        if len(head) < 8:
            return None
        box, kind = struct.unpack(">I4s", head[:8])
        hdr = 8
        if box == 1 and len(head) >= 16:
            box, hdr = struct.unpack(">Q", head[8:16])[0], 16
        elif box == 0:
            box = size - pos
        if box < hdr:
            return None
        if kind == b"mdat":
            return pos + hdr, min(pos + box, size)
        pos += box
    return None


def _payload_range(path: Path) -> tuple[int, int]:
    """Byte range of the audio payload: mdat for MP4, frames between ID3v2 and
    ID3v1 for MP3, else the whole file."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        suffix = path.suffix.lower()
        if suffix in (".m4a", ".m4b", ".mp4"):
            rng = _mp4_payload(f, size)
            if rng:
                return rng
        elif suffix == ".mp3":
            start, end = 0, size
            head = f.read(10)
            if head[:3] == b"ID3" and len(head) == 10:
                tag = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) | \
                      ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
                start = 10 + tag + (10 if head[5] & 0x10 else 0)
            if size >= 128:
                f.seek(size - 128)
                if f.read(3) == b"TAG":
                    end = size - 128
            if start < end:
                return start, end
    return 0, size


def payload_len(path: Path) -> int:
    """Length of the audio payload (cheap: headers only)."""
    start, end = _payload_range(Path(path))
    return end - start


def audio_fingerprint(path: Path) -> str | None:
    """'<payload length>:<sha1 of start/middle/end samples of the payload>'."""
    path = Path(path)
    try:
        start, end = _payload_range(path)
        length = end - start
        h = hashlib.sha1(str(length).encode())
        with open(path, "rb") as f:
            if length <= 3 * _CHUNK:
                f.seek(start)
                h.update(f.read(length))
            else:
                for off in (start, start + length // 2 - _CHUNK // 2, end - _CHUNK):
                    f.seek(off)
                    h.update(f.read(_CHUNK))
    except OSError:
        return None
    return f"{length}:{h.hexdigest()}"


def fingerprint_asset(asset: Asset) -> str | None:
    """Store the fingerprint of the asset's current file (once)."""
    fp = (asset.extra or {}).get(_FP_KEY)
    if fp:
        return fp
    if not asset.file_path or not Path(asset.file_path).is_file():
        return None
    fp = audio_fingerprint(Path(asset.file_path))
    if fp:
        asset.extra = {**(asset.extra or {}), _FP_KEY: fp}
    return fp


_BATCH = 100
_RETRIES = 5
_sleep = time.sleep          # patched in tests


def _is_locked(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower() or "database is busy" in str(exc).lower()


def _with_retry(fn, attempts: int = _RETRIES):
    """Run *fn*; on a locked database wait 2, 4, 8 ... s and try again. The
    last failure is raised. Live 2026-11-18: one unlucky write collided with a
    sweep and the whole nightly job died without writing anything."""
    for n in range(attempts):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_locked(exc) or n == attempts - 1:
                raise
            _sleep(2 ** (n + 1))


def _write_fingerprints(session: Session, rows: list[dict]) -> None:
    """Store fingerprints without touching updated_at (it is a signal other
    code reads: a backfill is not an edit)."""
    stmt = (update(Asset).where(Asset.id == bindparam("b_id"))
            .values(extra=bindparam("b_extra", type_=Asset.extra.type),
                    updated_at=Asset.updated_at))
    session.connection().execute(stmt, rows)
    session.commit()


def backfill_fingerprints(session: Session, limit: int = 5000) -> int:
    """Fingerprint up to *limit* audio assets that have none (nightly,
    incremental). Writes small batches with retry; if the database stays
    locked it stops and reports what is already stored — never raises for a
    lock, the next run continues where this one ended."""
    todo = [(aid, path, extra) for aid, path, extra in session.query(
            Asset.id, Asset.file_path, Asset.extra)
            .filter(Asset.type == AssetType.AUDIO, Asset.status == AssetStatus.COMPLETE,
                    Asset.file_path.isnot(None)).order_by(Asset.id).all()
            if not (extra or {}).get(_FP_KEY)]
    done, batch = 0, []

    def flush() -> bool:
        nonlocal batch
        if not batch:
            return True
        rows, batch = batch, []
        try:
            _with_retry(lambda: _write_fingerprints(session, rows))
            return True
        except OperationalError as exc:
            session.rollback()
            log.warning("fingerprint_backfill_locked", pending=len(rows), error=str(exc)[:120])
            return False

    for aid, path, extra in todo:
        if done + len(batch) >= limit:
            break
        if not path or not Path(path).is_file():
            continue
        fp = audio_fingerprint(Path(path))
        if not fp:
            continue
        batch.append({"b_id": aid, "b_extra": {**(extra or {}), _FP_KEY: fp}})
        if len(batch) >= _BATCH:
            n = len(batch)
            if not flush():
                return done
            done += n
    n = len(batch)
    if flush():
        done += n
    return done


# --- folder name -> metadata ----------------------------------------------------

_DIR_RE = re.compile(
    r"^(?P<author>.+?) - (?:\((?P<year>\d{4})\) )?(?P<title>[^()]+?)"
    r"(?: \((?:cte (?P<narrator>[^,()]+?))?(?:, )?(?P<pub>CRo\d?(?: \d{4})?)?\))?$")


def parse_book_dir(name: str) -> dict[str, str] | None:
    """'Autor - (rok) Titul (cte Narrator, CRo 1989)' -> fields, else None."""
    m = _DIR_RE.match(name.strip())
    if not m or not m.group("title").strip():
        return None
    out = {"author": m.group("author").strip(), "title": m.group("title").strip()}
    for key, group in (("year", "year"), ("narrator", "narrator"), ("publisher", "pub")):
        if m.group(group):
            out[key] = m.group(group).strip()
    if "publisher" in out and not re.search(r"\d{4}", out["publisher"]):
        del out["publisher"]          # a bare "CRo" says nothing about the recording
    return out


def _adopt_folder_name(session: Session, work: Work, folder: Path) -> None:
    parsed = parse_book_dir(folder.name)
    if not parsed:
        return
    src = "folder-name"
    for field_name in ("title", "author", "year", "publisher"):
        if field_name in parsed:
            record_value(session, "work", work.id, field_name, parsed[field_name],
                         FieldOrigin.MANUAL, src)
    if "narrator" in parsed:
        for ep in work.episodes:
            record_value(session, "episode", ep.id, "narrator", parsed["narrator"],
                         FieldOrigin.MANUAL, src)


# --- following ------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    asset_id: int
    old: str
    new: str


@dataclass
class FollowReport:
    moved: list[Move] = field(default_factory=list)
    ambiguous: list[tuple[int, list[str]]] = field(default_factory=list)
    unmatched: list[tuple[int, str]] = field(default_factory=list)
    works_updated: list[tuple[int, str, str]] = field(default_factory=list)


def _is_dead(asset: Asset) -> bool:
    return not asset.file_path or not os.path.exists(asset.file_path)


def _walk_audio(roots: list[Path]):
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if Path(fn).suffix.lower() in AUDIO_SUFFIXES:
                    yield Path(dirpath) / fn


def _final_path(session: Session, work_id: int) -> str | None:
    from audiobiblio.library.pipelines.auto_finalize import _resolved_value
    return _resolved_value(session, "work", work_id, "final_path")


def follow_paths(session: Session, roots: list[Path], apply: bool = False) -> FollowReport:
    """Re-point assets whose files were renamed or moved. Dry run by default."""
    report = FollowReport()
    dead = [a for a in session.query(Asset).filter(
        Asset.type == AssetType.AUDIO,
        Asset.status.in_((AssetStatus.COMPLETE, AssetStatus.MISSING)),
        Asset.file_path.isnot(None)).all()
        if _is_dead(a) and (a.extra or {}).get(_FP_KEY)]
    if not dead:
        return report

    by_len: dict[int, list[Asset]] = defaultdict(list)
    for a in dead:
        by_len[int(a.extra[_FP_KEY].split(":")[0])].append(a)
    known = {p for (p,) in session.query(Asset.file_path).filter(Asset.file_path.isnot(None))}

    found: dict[str, list[Path]] = defaultdict(list)
    for path in _walk_audio(roots):
        if str(path) in known:
            continue
        try:
            plen = payload_len(path)
        except OSError:
            continue
        if plen in by_len:
            fp = audio_fingerprint(path)
            if fp:
                found[fp].append(path)

    dead_by_fp: dict[str, list[Asset]] = defaultdict(list)
    for a in dead:
        dead_by_fp[a.extra[_FP_KEY]].append(a)

    touched: dict[int, set[str]] = defaultdict(set)   # work_id -> old dirs
    for fp, assets in dead_by_fp.items():
        paths = found.get(fp, [])
        if not paths:
            report.unmatched += [(a.id, a.file_path) for a in assets]
        elif len(paths) > 1 or len(assets) > 1:
            report.ambiguous += [(a.id, [str(p) for p in paths]) for a in assets]
        else:
            asset, new = assets[0], paths[0]
            report.moved.append(Move(asset.id, asset.file_path, str(new)))
            if apply:
                _apply_move(session, asset, new, touched)
    if apply:
        for work_id, old_dirs in touched.items():
            _update_work_folder(session, work_id, old_dirs, report)
        session.commit()
    elif report.moved:
        pass
    return report


def _apply_move(session: Session, asset: Asset, new: Path, touched: dict[int, set[str]]) -> None:
    old = Path(asset.file_path)
    history = list((asset.extra or {}).get(_HISTORY_KEY, []))[-19:]
    history.append({"from": str(old), "to": str(new), "at": utcnow().isoformat()})
    asset.extra = {**(asset.extra or {}), _HISTORY_KEY: history}
    asset.file_path = str(new)
    asset.status = AssetStatus.COMPLETE
    ep = session.get(Episode, asset.episode_id)
    touched[ep.work_id].add(str(old.parent))
    # sidecars of the same episode that sat next to the old file follow it
    for side in session.query(Asset).filter(Asset.episode_id == asset.episode_id,
                                            Asset.id != asset.id,
                                            Asset.file_path.isnot(None)).all():
        sp = Path(side.file_path)
        if sp.parent == old.parent and not sp.exists() and (new.parent / sp.name).exists():
            side.file_path = str(new.parent / sp.name)
    session.flush()


def _update_work_folder(session: Session, work_id: int, old_dirs: set[str],
                        report: FollowReport) -> None:
    work = session.get(Work, work_id)
    current = _final_path(session, work_id)
    if not current or os.path.isdir(current):
        return
    dirs = {str(Path(a.file_path).parent) for e in work.episodes for a in e.assets
            if a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
            and a.file_path and os.path.exists(a.file_path)}
    if len(dirs) != 1:
        return
    (new_dir,) = dirs
    record_value(session, "work", work_id, "final_path", new_dir, FieldOrigin.MANUAL, "disk-follow")
    report.works_updated.append((work_id, current, new_dir))
    _adopt_folder_name(session, work, Path(new_dir))
    session.flush()
