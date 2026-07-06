"""
File path verification and reconciliation for assets.

Detects assets whose file_path no longer exists on disk and optionally marks them MISSING.
Useful for handling dead files after disk reorganization or cleanup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from audiobiblio.core.db.models import Asset, AssetStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FileCheckReport:
    """Report from verify_asset_paths: counts and list of missing assets."""

    checked: int
    """Total COMPLETE assets with file_path checked."""

    ok: int
    """Assets where file_path exists."""

    missing: list[tuple[int, str]]
    """List of (asset_id, file_path) pairs for missing files."""


def verify_asset_paths(
    session: Session,
    limit: int | None = None,
    fix: bool = False,
) -> FileCheckReport:
    """
    Verify file paths of COMPLETE assets; optionally mark missing ones.

    For each COMPLETE asset with a non-NULL file_path:
      - Expand ~ and env vars, check if Path exists
      - If missing and fix=True: set status=MISSING, stash path in extra["last_known_path"]
      - file_path is left untouched (documents where the file was)

    Args:
        session: SQLAlchemy session
        limit: Max assets to check (default: all)
        fix: If True, update DB (set status=MISSING); if False, dry-run only

    Returns:
        FileCheckReport with checked, ok, and missing counts/list
    """
    q = select(Asset).where(
        Asset.status == AssetStatus.COMPLETE,
        Asset.file_path.isnot(None),
    )

    if limit:
        assets = session.execute(q.limit(limit)).scalars().all()
    else:
        assets = session.execute(q).scalars().all()

    checked = 0
    ok = 0
    missing_list: list[tuple[int, str]] = []

    for asset in assets:
        checked += 1
        file_path = asset.file_path
        assert file_path is not None  # precondition from the query

        try:
            # Expand environment variables first, then ~ and relative paths
            expanded_str = os.path.expandvars(file_path)
            expanded_path = Path(expanded_str).expanduser()
            exists = expanded_path.exists()
        except (OSError, RuntimeError):
            # Path may be invalid; treat as missing
            exists = False

        if exists:
            ok += 1
        else:
            missing_list.append((asset.id, file_path))
            if fix:
                # Mark as MISSING and stash the path
                asset.status = AssetStatus.MISSING
                # Merge into extra dict without clobbering
                asset.extra = {
                    **(asset.extra or {}),
                    "last_known_path": file_path,
                }
                session.add(asset)

    if fix and missing_list:
        session.commit()

    return FileCheckReport(
        checked=checked,
        ok=ok,
        missing=missing_list,
    )
