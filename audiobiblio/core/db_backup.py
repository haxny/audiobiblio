"""Scheduled backup of the SQLite DB: consistent copy, checked, compressed, pruned.

A LOGICAL safety net (a mistaken bulk edit, a corrupt page): the copy sits on
the same volume as the DB, so it does not replace an off-box backup.

Steps: sqlite online-backup into a temp file (consistent even with a live WAL),
`PRAGMA quick_check` on the copy, gzip, then prune old backups. Anything that
fails leaves no partial file and never prunes (an old good backup is only
removed after a new good one exists).
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

log = structlog.get_logger()

PREFIX, SUFFIX = "db-", ".sqlite3.gz"
MIN_FREE_FACTOR = 2.0          # free space must cover 2x the DB (copy + archive)


class BackupError(RuntimeError):
    """The copy failed its integrity check or could not be written."""


class BackupSkipped(RuntimeError):
    """Not done on purpose (not enough free space)."""


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    pruned: list[Path] = field(default_factory=list)


def _quick_check(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return str(con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()


def _copy_consistent(src: Path, dst: Path) -> None:
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup_database(db_path: Path, dest_dir: Path, keep: int = 3,
                    now: datetime | None = None) -> BackupResult:
    db_path, dest_dir = Path(db_path), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    need = int(db_path.stat().st_size * MIN_FREE_FACTOR)
    free = shutil.disk_usage(dest_dir).free
    if free < need:
        raise BackupSkipped(f"{free / 1e9:.1f} GB free < {need / 1e9:.1f} GB needed")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final = dest_dir / f"{PREFIX}{stamp}{SUFFIX}"
    tmp = dest_dir / f".tmp-{stamp}.sqlite3"
    part = dest_dir / f".tmp-{stamp}.gz"
    try:
        _copy_consistent(db_path, tmp)
        verdict = _quick_check(tmp)
        if verdict != "ok":
            raise BackupError(f"quick_check of the copy failed: {verdict[:120]}")
        with open(tmp, "rb") as f, gzip.open(part, "wb", compresslevel=3) as out:
            shutil.copyfileobj(f, out, 1024 * 1024)
        part.replace(final)
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    pruned = _prune(dest_dir, keep)
    res = BackupResult(final, final.stat().st_size, pruned)
    log.info("db_backup_done", path=str(final), size_mb=round(res.size_bytes / 1e6),
             pruned=len(pruned))
    return res


def _prune(dest_dir: Path, keep: int) -> list[Path]:
    """Delete backups beyond the *keep* newest (by name = by time). Only files
    matching our own naming are touched."""
    mine = sorted((p for p in dest_dir.iterdir()
                   if p.name.startswith(PREFIX) and p.name.endswith(SUFFIX)),
                  key=lambda p: p.name, reverse=True)
    removed = mine[max(keep, 1):]
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


def verify_backup(path: Path) -> bool:
    """Read the whole archive (gzip checks its CRC at the end): True if intact."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1024 * 1024):
                pass
        return True
    except (OSError, EOFError):
        return False
