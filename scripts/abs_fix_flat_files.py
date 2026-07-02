"""
Fix flat audio/video files in author-level directories.

For each file sitting directly in an author folder (not in a book subfolder),
creates a subfolder named after the file (without extension) and moves the
file into it. This makes ABS correctly parse Author/Book structure.

Usage (on NAS or via SSH):
    python3 scripts/abs_fix_flat_files.py /volume3/eBOOKs/eBOOKs.fiction --dry-run
    python3 scripts/abs_fix_flat_files.py /volume3/eBOOKs/eBOOKs.fiction
    python3 scripts/abs_fix_flat_files.py /volume3/eBOOKs/4kids --dry-run --flat
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

AUDIO_VIDEO_EXTS = {
    ".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wma", ".wav",
    ".mp4", ".mkv", ".avi", ".webm",
}

# Files that should be grouped into a single book folder.
# Key = target folder name, Value = list of filename prefixes to match.
GROUPINGS: dict[str, list[str]] = {
    "John Steinbeck - (1937) O mysich a lidech": [
        "John Steinbeck - (1937) O mysich a lidech",
    ],
}


def _find_group(filename: str) -> str | None:
    """Check if a filename matches a grouping rule, return target folder name."""
    for target, prefixes in GROUPINGS.items():
        for prefix in prefixes:
            if filename.startswith(prefix):
                return target
    return None


def find_flat_files(library_root: Path, flat_library: bool = False) -> list[tuple[Path, Path]]:
    """
    Find files that need to be moved into book subfolders.

    For Author/Book libraries: finds files directly in author-level dirs.
    For flat libraries (--flat): finds files directly in library root.

    Returns list of (current_path, target_dir).
    """
    moves: list[tuple[Path, Path]] = []

    if flat_library:
        # Flat library: files directly in library root
        for entry in sorted(library_root.iterdir()):
            if entry.is_file() and entry.suffix.lower() in AUDIO_VIDEO_EXTS:
                book_dir = library_root / entry.stem
                moves.append((entry, book_dir))
        return moves

    # Author/Book library: check each author dir for flat files
    for author_dir in sorted(library_root.iterdir()):
        if not author_dir.is_dir():
            continue
        # Skip Synology metadata
        if author_dir.name == "@eaDir":
            continue

        try:
            entries = sorted(author_dir.iterdir())
        except PermissionError:
            print(f"  SKIP (permission denied): {author_dir.name}", file=sys.stderr)
            continue

        for entry in entries:
            try:
                is_file = entry.is_file()
            except PermissionError:
                continue
            if is_file and entry.suffix.lower() in AUDIO_VIDEO_EXTS:
                # Check if file matches a grouping rule
                target_name = _find_group(entry.name)
                if target_name:
                    book_dir = author_dir / target_name
                else:
                    book_dir = author_dir / entry.stem
                moves.append((entry, book_dir))

    return moves


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move flat audio files into book subfolders for ABS"
    )
    parser.add_argument("library_root", help="Path to the library directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--flat", action="store_true",
                        help="Treat as flat library (files in root, not Author/Book)")
    args = parser.parse_args()

    library_root = Path(args.library_root)
    if not library_root.is_dir():
        print(f"ERROR: {library_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    moves = find_flat_files(library_root, args.flat)

    if not moves:
        print("No flat files found — nothing to do.")
        return

    print(f"Found {len(moves)} files to move:\n")

    moved = 0
    errors = 0

    for file_path, book_dir in moves:
        target = book_dir / file_path.name
        rel_file = file_path.relative_to(library_root)
        rel_target = target.relative_to(library_root)

        if args.dry_run:
            print(f"  {rel_file}")
            print(f"    -> {rel_target}")
            print()
            moved += 1
            continue

        try:
            book_dir.mkdir(exist_ok=True)
            shutil.move(str(file_path), str(target))
            print(f"  MOVED: {rel_file}")
            print(f"     -> {rel_target}")
            moved += 1
        except OSError as e:
            print(f"  ERROR: {rel_file} — {e}", file=sys.stderr)
            errors += 1

    action = "Would move" if args.dry_run else "Moved"
    print(f"\nDone: {action} {moved} files, {errors} errors.")


if __name__ == "__main__":
    main()
