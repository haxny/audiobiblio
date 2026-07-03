"""
trash — Move files to dated trash with restoration info; purge aged trash.

Files are NEVER deleted directly; they move to trash with a sidecar
containing original path, reason, and timestamp for potential restoration.
Only purge_trash() permanently deletes, and only date-folders older than
retention cutoff.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path


def move_to_trash(
    path: Path,
    library_dir: Path,
    reason: str = "",
    now: datetime | None = None,
) -> Path:
    """
    Move file to {library_dir}/.trash/{YYYY-MM-DD}/{name}.

    Writes sidecar {name}.trashinfo.json with original_path, reason, trashed_at.
    Handles name collisions with -2, -3 suffixes before extension.

    Args:
        path: File or folder to move to trash.
        library_dir: Library root directory.
        reason: Why the file was trashed (optional).
        now: Current datetime for testability; defaults to datetime.now().

    Returns:
        Path to the trashed file/folder.

    Raises:
        ValueError: If path is already inside .trash.
    """
    if now is None:
        now = datetime.now()

    path = Path(path).resolve()
    library_dir = Path(library_dir).resolve()

    # Check if path is already in trash
    try:
        path.relative_to(library_dir / ".trash")
        raise ValueError(f"Path {path} is already in trash")
    except ValueError as e:
        if "already in trash" in str(e):
            raise
        # "not in the subpath" error means it's not in .trash, which is good

    # Create dated trash folder
    date_str = now.strftime("%Y-%m-%d")
    trash_root = library_dir / ".trash" / date_str
    trash_root.mkdir(parents=True, exist_ok=True)

    # Handle name collisions
    trash_path = trash_root / path.name
    if trash_path.exists():
        # Add suffix -2, -3, etc. before extension
        name_parts = path.name.rsplit(".", 1)
        if len(name_parts) == 2:
            base, ext = name_parts
            ext = "." + ext
        else:
            base = name_parts[0]
            ext = ""

        counter = 2
        while True:
            trash_path = trash_root / f"{base}-{counter}{ext}"
            if not trash_path.exists():
                break
            counter += 1

    # Move file to trash
    shutil.move(str(path), str(trash_path))

    # Write sidecar with original path and reason
    sidecar_path = trash_path.parent / f"{trash_path.name}.trashinfo.json"
    sidecar_data = {
        "original_path": str(path.absolute()),
        "reason": reason,
        "trashed_at": now.isoformat(),
    }
    with open(sidecar_path, "w") as f:
        json.dump(sidecar_data, f, indent=2)

    return trash_path


def purge_trash(
    library_dir: Path,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """
    Remove date-folders strictly older than retention cutoff.

    Folders named YYYY-MM-DD older than (now - retention_days) are deleted
    entirely, including all their contents.

    Args:
        library_dir: Library root directory.
        retention_days: Days to keep trash (e.g., 30).
        now: Current datetime for testability; defaults to datetime.now().

    Returns:
        Count of removed date-folders.
    """
    if now is None:
        now = datetime.now()

    library_dir = Path(library_dir).resolve()
    trash_root = library_dir / ".trash"

    if not trash_root.exists():
        return 0

    # Calculate cutoff date: anything strictly older than this is deleted
    cutoff_date = now - timedelta(days=retention_days)

    removed_count = 0
    for date_folder in trash_root.iterdir():
        if not date_folder.is_dir():
            continue

        try:
            # Parse folder name as YYYY-MM-DD
            folder_date = datetime.strptime(date_folder.name, "%Y-%m-%d").date()
            folder_datetime = datetime.combine(folder_date, datetime.min.time())

            # Remove if strictly older than cutoff
            if folder_datetime < cutoff_date:
                shutil.rmtree(date_folder)
                removed_count += 1
        except ValueError:
            # Not a valid date folder, skip it
            pass

    return removed_count
