"""
Tests for audiobiblio.library.trash

TDD — write tests first (RED), implement to make them GREEN.

Covers:
  1. move_to_trash() creates dated folder structure with sidecar JSON
  2. move_to_trash() handles collision with suffix (-2, -3, etc.)
  3. move_to_trash() raises ValueError if path is already in .trash
  4. purge_trash() removes only folders older than retention cutoff
  5. purge_trash() returns count of removed folders
  6. purge_trash() accepts `now` parameter for testability
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from audiobiblio.library.trash import move_to_trash, purge_trash


# ---------------------------------------------------------------------------
# Test 1: move_to_trash() creates dated folder and sidecar
# ---------------------------------------------------------------------------

def test_move_to_trash_creates_dated_folder(tmp_path: Path) -> None:
    """File moves to {library_dir}/.trash/YYYY-MM-DD/{name}."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    source = library_dir / "test_file.txt"
    source.write_text("test content")

    trash_path = move_to_trash(source, library_dir, reason="testing")

    # Source should be gone
    assert not source.exists()

    # Trashed path should exist
    assert trash_path.exists()

    # Should be in .trash/{YYYY-MM-DD}/ folder
    assert ".trash" in str(trash_path)
    assert trash_path.name == "test_file.txt"

    # Content should be preserved
    assert trash_path.read_text() == "test content"


def test_move_to_trash_creates_sidecar(tmp_path: Path) -> None:
    """Sidecar {name}.trashinfo.json contains original_path, reason, trashed_at."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    source = library_dir / "test_file.txt"
    source.write_text("test")
    original_path = str(source.absolute())

    trash_path = move_to_trash(source, library_dir, reason="user deleted it")

    sidecar_path = trash_path.parent / f"{trash_path.name}.trashinfo.json"
    assert sidecar_path.exists()

    with open(sidecar_path) as f:
        data = json.load(f)

    assert data["original_path"] == original_path
    assert data["reason"] == "user deleted it"
    assert "trashed_at" in data
    # trashed_at should be ISO format
    datetime.fromisoformat(data["trashed_at"])


def test_move_to_trash_dated_folder_format(tmp_path: Path) -> None:
    """Dated folder is YYYY-MM-DD format."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    source = library_dir / "test.txt"
    source.write_text("test")

    now = datetime(2025, 3, 15, 12, 30, 45)
    trash_path = move_to_trash(source, library_dir, now=now)

    # Should contain .trash/2025-03-15/
    trash_root = library_dir / ".trash"
    date_folder = trash_root / "2025-03-15"

    assert date_folder.exists()
    assert (date_folder / "test.txt").exists()


# ---------------------------------------------------------------------------
# Test 2: move_to_trash() collision handling with suffix
# ---------------------------------------------------------------------------

def test_move_to_trash_collision_suffix(tmp_path: Path) -> None:
    """Multiple files with same name get -2, -3 suffixes."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash" / "2025-03-15"
    trash_dir.mkdir(parents=True)

    # Pre-create first file
    (trash_dir / "test.txt").write_text("first")

    # Move two files with same name
    source1 = library_dir / "test.txt"
    source1.write_text("second")

    source2 = library_dir / "test.txt"
    source2.write_text("second")

    now = datetime(2025, 3, 15)
    trash_path1 = move_to_trash(source1, library_dir, now=now)

    # Should have -2 suffix before extension
    assert trash_path1.name == "test-2.txt"
    assert trash_path1.exists()


def test_move_to_trash_multiple_collisions(tmp_path: Path) -> None:
    """Multiple collisions get -2, -3, -4, etc."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash" / "2025-03-15"
    trash_dir.mkdir(parents=True)

    # Pre-create first two
    (trash_dir / "file.txt").write_text("1")
    (trash_dir / "file-2.txt").write_text("2")

    # Try to trash a third
    source = library_dir / "file.txt"
    source.write_text("3")

    now = datetime(2025, 3, 15)
    trash_path = move_to_trash(source, library_dir, now=now)

    assert trash_path.name == "file-3.txt"
    assert trash_path.exists()


def test_move_to_trash_suffix_no_extension(tmp_path: Path) -> None:
    """Suffix added before extension (file-2.ext, not file.ext-2)."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash" / "2025-03-15"
    trash_dir.mkdir(parents=True)

    (trash_dir / "README").write_text("original")

    source = library_dir / "README"
    source.write_text("collision")

    now = datetime(2025, 3, 15)
    trash_path = move_to_trash(source, library_dir, now=now)

    # Should be README-2, not README.-2
    assert trash_path.name == "README-2"


# ---------------------------------------------------------------------------
# Test 3: move_to_trash() refuses to trash files already in .trash
# ---------------------------------------------------------------------------

def test_move_to_trash_raises_on_already_trashed(tmp_path: Path) -> None:
    """ValueError if path is already inside .trash."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash" / "2025-03-15"
    trash_dir.mkdir(parents=True)

    trashed_file = trash_dir / "test.txt"
    trashed_file.write_text("already in trash")

    with pytest.raises(ValueError, match="already.*trash"):
        move_to_trash(trashed_file, library_dir)


def test_move_to_trash_raises_for_trash_subfolder(tmp_path: Path) -> None:
    """ValueError for any path inside .trash, even nested."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash" / "2025-03-15"
    trash_dir.mkdir(parents=True)

    nested = trash_dir / "subfolder" / "file.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("test")

    with pytest.raises(ValueError):
        move_to_trash(nested, library_dir)


# ---------------------------------------------------------------------------
# Test 4: purge_trash() removes only old folders
# ---------------------------------------------------------------------------

def test_purge_trash_removes_old_folders(tmp_path: Path) -> None:
    """purge_trash() removes date folders older than retention_days."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    # Create folders for different dates
    old_folder = trash_dir / "2025-02-01"
    old_folder.mkdir()
    (old_folder / "old_file.txt").write_text("old")

    recent_folder = trash_dir / "2025-03-15"
    recent_folder.mkdir()
    (recent_folder / "recent_file.txt").write_text("recent")

    # Cutoff is 30 days before now
    now = datetime(2025, 3, 20)
    retention_days = 30

    count = purge_trash(library_dir, retention_days, now=now)

    # 2025-02-01 is 47 days before 2025-03-20, so it should be removed
    assert not old_folder.exists()

    # 2025-03-15 is 5 days before cutoff, so it should remain
    assert recent_folder.exists()

    assert count == 1


def test_purge_trash_keeps_recent_folders(tmp_path: Path) -> None:
    """Folders within retention period are kept."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    # All within 30 days
    folder1 = trash_dir / "2025-03-10"
    folder1.mkdir()
    (folder1 / "file.txt").write_text("test")

    folder2 = trash_dir / "2025-03-15"
    folder2.mkdir()
    (folder2 / "file.txt").write_text("test")

    now = datetime(2025, 3, 20)
    count = purge_trash(library_dir, retention_days=30, now=now)

    assert folder1.exists()
    assert folder2.exists()
    assert count == 0


def test_purge_trash_returns_count(tmp_path: Path) -> None:
    """purge_trash() returns number of removed folders."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    # Create 3 old folders
    for i in range(1, 4):
        folder = trash_dir / f"2025-01-{i:02d}"
        folder.mkdir()
        (folder / "file.txt").write_text("old")

    # Create 2 recent folders
    for i in range(15, 17):
        folder = trash_dir / f"2025-03-{i:02d}"
        folder.mkdir()
        (folder / "file.txt").write_text("recent")

    now = datetime(2025, 3, 20)
    count = purge_trash(library_dir, retention_days=30, now=now)

    # All 3 January folders should be removed (>30 days old)
    assert count == 3
    assert not (trash_dir / "2025-01-01").exists()
    assert not (trash_dir / "2025-01-02").exists()
    assert not (trash_dir / "2025-01-03").exists()

    # Recent folders should remain
    assert (trash_dir / "2025-03-15").exists()
    assert (trash_dir / "2025-03-16").exists()


# ---------------------------------------------------------------------------
# Test 5: purge_trash() with now parameter
# ---------------------------------------------------------------------------

def test_purge_trash_accepts_now_parameter(tmp_path: Path) -> None:
    """purge_trash() uses provided `now` for testability."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    old_folder = trash_dir / "2025-01-01"
    old_folder.mkdir()
    (old_folder / "file.txt").write_text("old")

    # With now=2025-03-20 and retention=30, 2025-01-01 should be removed
    # Cutoff = 2025-03-20 - 30 days = 2025-02-18
    # 2025-01-01 < 2025-02-18, so it's old
    now = datetime(2025, 3, 20)
    count = purge_trash(library_dir, retention_days=30, now=now)

    assert not old_folder.exists()
    assert count == 1


# ---------------------------------------------------------------------------
# Test 6: Edge cases
# ---------------------------------------------------------------------------

def test_purge_trash_with_no_trash_dir(tmp_path: Path) -> None:
    """purge_trash() handles missing .trash gracefully."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    # .trash doesn't exist
    count = purge_trash(library_dir, retention_days=30)

    assert count == 0


def test_move_to_trash_with_reason_empty_string(tmp_path: Path) -> None:
    """move_to_trash() accepts empty reason string."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    source = library_dir / "test.txt"
    source.write_text("test")

    trash_path = move_to_trash(source, library_dir, reason="")

    sidecar = trash_path.parent / f"{trash_path.name}.trashinfo.json"
    with open(sidecar) as f:
        data = json.load(f)

    assert data["reason"] == ""
    assert trash_path.exists()


# ---------------------------------------------------------------------------
# Test 7: Boundary-exact purge cutoff
# ---------------------------------------------------------------------------

def test_purge_keeps_folder_exactly_at_cutoff(tmp_path: Path) -> None:
    """Folder dated exactly at cutoff is KEPT (not removed)."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    # Create folder dated exactly at cutoff
    # retention_days=30, now=2025-03-20 -> cutoff=2025-02-18
    # Folder dated 2025-02-18 should be KEPT (not strictly older)
    cutoff_folder = trash_dir / "2025-02-18"
    cutoff_folder.mkdir()
    (cutoff_folder / "file.txt").write_text("at cutoff")

    # Also create a folder strictly older
    old_folder = trash_dir / "2025-02-17"
    old_folder.mkdir()
    (old_folder / "file.txt").write_text("older")

    now = datetime(2025, 3, 20)
    count = purge_trash(library_dir, retention_days=30, now=now)

    # Cutoff folder should be kept
    assert cutoff_folder.exists()
    # Old folder should be removed
    assert not old_folder.exists()
    # Only one folder removed
    assert count == 1


def test_purge_skips_non_date_folders(tmp_path: Path) -> None:
    """Folders with non-date names survive purge without crashing or being counted."""
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    trash_dir = library_dir / ".trash"
    trash_dir.mkdir()

    # Create a date folder (old)
    old_folder = trash_dir / "2025-01-01"
    old_folder.mkdir()
    (old_folder / "file.txt").write_text("old")

    # Create a non-date folder
    non_date_folder = trash_dir / "notadate"
    non_date_folder.mkdir()
    (non_date_folder / "file.txt").write_text("not a date")

    # Create another non-date folder with special chars
    special_folder = trash_dir / "2025_invalid"
    special_folder.mkdir()
    (special_folder / "file.txt").write_text("special")

    now = datetime(2025, 3, 20)
    count = purge_trash(library_dir, retention_days=30, now=now)

    # Old date folder should be removed
    assert not old_folder.exists()
    # Non-date folders should survive
    assert non_date_folder.exists()
    assert special_folder.exists()
    # Only one folder removed (the old date folder)
    assert count == 1
