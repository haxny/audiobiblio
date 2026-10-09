"""Files the user must be able to edit (foobar over SMB).

The container runs as root, so everything it created or moved used to be
root:root 644 — read-only for the user (2026-10-09: 77k files). Every write
path hands the result to the owner of its parent folder, group-writable.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog

log = structlog.get_logger()

FILE_MODE, DIR_MODE = 0o664, 0o775


def adopt_parent_owner(path: Path | str) -> int:
    """chown path (recursively for a dir) to its parent's uid/gid and make it
    group-writable. Returns how many entries changed. No-op when not root."""
    p = Path(path)
    if not p.exists() or os.geteuid() != 0:
        return 0
    st = p.parent.stat()
    changed = 0
    entries = [p] + (list(p.rglob("*")) if p.is_dir() else [])
    for e in entries:
        if "@eaDir" in e.parts:
            continue
        try:
            es = e.lstat()
            mode = DIR_MODE if e.is_dir() else FILE_MODE
            if (es.st_uid, es.st_gid) != (st.st_uid, st.st_gid):
                os.lchown(e, st.st_uid, st.st_gid)
                changed += 1
            if not e.is_symlink() and (es.st_mode & 0o777) != mode:
                os.chmod(e, mode)
        except OSError as exc:
            log.warning("adopt_owner_failed", path=str(e), error=str(exc))
    return changed


def fix_root_owned(root: Path) -> int:
    """Nightly safety net: every root-owned entry under root → parent owner."""
    if os.geteuid() != 0 or not root.is_dir():
        return 0
    fixed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("@eaDir", "#recycle")]
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            try:
                if p.lstat().st_uid == 0:
                    fixed += adopt_parent_owner(p) if not p.is_dir() else _adopt_one(p)
            except OSError:
                continue
    return fixed


def _adopt_one(p: Path) -> int:
    st = p.parent.stat()
    try:
        os.lchown(p, st.st_uid, st.st_gid)
        os.chmod(p, DIR_MODE)
        return 1
    except OSError:
        return 0
