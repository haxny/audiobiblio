#!/usr/bin/env python3
"""
Normalize folder and file names by stripping diacritics to ASCII.

Creates a JSON changelog for rollback. Does NOT touch file contents or tags.

Usage (run ON the NAS):
    python3 scripts/abs_normalize_names.py /volume3/eBOOKs/eBOOKs.fiction --dry-run
    python3 scripts/abs_normalize_names.py /volume3/eBOOKs/eBOOKs.fiction
    python3 scripts/abs_normalize_names.py /volume3/eBOOKs/eBOOKs.fiction --rollback changelog-20260421.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Czech diacritics substitution table
_CZECH_MAP = {
    'á': 'a', 'č': 'c', 'ď': 'd', 'é': 'e', 'ě': 'e', 'í': 'i',
    'ň': 'n', 'ó': 'o', 'ř': 'r', 'š': 's', 'ť': 't', 'ú': 'u',
    'ů': 'u', 'ý': 'y', 'ž': 'z',
    'Á': 'A', 'Č': 'C', 'Ď': 'D', 'É': 'E', 'Ě': 'E', 'Í': 'I',
    'Ň': 'N', 'Ó': 'O', 'Ř': 'R', 'Š': 'S', 'Ť': 'T', 'Ú': 'U',
    'Ů': 'U', 'Ý': 'Y', 'Ž': 'Z',
}

# Windows-1250 corrupted diacritics
_CORRUPTED_MAP = {
    'ì': 'e', 'è': 'c', 'ï': 'd', 'ò': 'n', 'ø': 'r',
    '¹': 's', '»': 't', '¾': 'z',
}

_COMBINED_MAP = {**_CZECH_MAP, **_CORRUPTED_MAP}


def strip_diacritics(text: str) -> str:
    """Remove diacritics from text (handles Czech + general Unicode)."""
    if not text:
        return text
    # Replace Unicode dashes/quotes with ASCII equivalents before stripping
    text = text.replace('–', '-')  # en-dash
    text = text.replace('—', '-')  # em-dash
    text = text.replace('…', '...')  # ellipsis
    text = text.replace('\u2018', "'").replace('\u2019', "'")  # smart quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    for old, new in _COMBINED_MAP.items():
        text = text.replace(old, new)
    # Unicode normalization for remaining diacritics (French, German, etc.)
    text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
    return text


def needs_normalization(name: str) -> bool:
    """Check if a name contains non-ASCII characters."""
    try:
        name.encode('ascii')
        return False
    except UnicodeEncodeError:
        return True


def collect_renames(root: Path) -> list[tuple[Path, Path]]:
    """
    Collect all renames needed, bottom-up (deepest first).

    Bottom-up is critical: rename files before their parent directories,
    otherwise the parent path changes and file renames fail.
    """
    renames: list[tuple[Path, Path]] = []

    # Walk the tree and collect at each level
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=False):
        dirpath = Path(dirpath)

        # Skip Synology metadata
        if "@eaDir" in str(dirpath):
            continue

        # Files first (we're already bottom-up from os.walk)
        for fname in sorted(filenames):
            if not needs_normalization(fname):
                continue
            old = dirpath / fname
            new_name = strip_diacritics(fname)
            new = dirpath / new_name
            if old != new:
                renames.append((old, new))

        # Then directories (already bottom-up)
        for dname in sorted(dirnames):
            if dname.startswith(("@", ".")):
                continue
            if not needs_normalization(dname):
                continue
            old = dirpath / dname
            new_name = strip_diacritics(dname)
            new = dirpath / new_name
            if old != new:
                renames.append((old, new))

    return renames


def handle_conflict(new_path: Path) -> Path:
    """If target exists, append _2, _3, etc."""
    if not new_path.exists():
        return new_path
    stem = new_path.stem
    suffix = new_path.suffix
    parent = new_path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def do_rollback(changelog_path: str) -> None:
    """Reverse renames from a changelog file."""
    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog = json.load(f)

    entries = changelog.get("renames", [])
    # Rollback in reverse order (top-down: dirs before files)
    entries.reverse()

    restored = 0
    errors = 0
    for entry in entries:
        new_path = Path(entry["new"])
        old_path = Path(entry["old"])

        if not new_path.exists():
            print(f"  SKIP (not found): {new_path}", file=sys.stderr)
            continue

        try:
            os.rename(str(new_path), str(old_path))
            restored += 1
        except OSError as e:
            print(f"  ERROR: {new_path} -> {old_path}: {e}", file=sys.stderr)
            errors += 1

    print(f"\nRollback done: restored {restored}, errors {errors}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize folder/file names to ASCII")
    parser.add_argument("library_root", help="Path to the library directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be renamed")
    parser.add_argument("--rollback", help="Path to changelog JSON to reverse", metavar="FILE")
    args = parser.parse_args()

    if args.rollback:
        do_rollback(args.rollback)
        return

    root = Path(args.library_root)
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {root} for names with diacritics...")
    renames = collect_renames(root)
    print(f"Found {len(renames)} items to rename\n")

    if not renames:
        return

    if args.dry_run:
        for old, new in renames[:50]:
            rel_old = old.relative_to(root)
            rel_new = new.relative_to(root)
            if str(rel_old) != str(rel_new):
                print(f"  {rel_old}")
                print(f"    -> {rel_new}")
        if len(renames) > 50:
            print(f"  ... and {len(renames) - 50} more")
        print(f"\nTotal: {len(renames)} renames")
        return

    # Prepare changelog
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    changelog_path = root / f"_changelog-normalize-{timestamp}.json"
    changelog_entries: list[dict] = []

    renamed = 0
    skipped = 0
    errors = 0

    for old, new in renames:
        # Handle conflicts (target already exists)
        new = handle_conflict(new)

        try:
            os.rename(str(old), str(new))
            changelog_entries.append({
                "old": str(old),
                "new": str(new),
            })
            renamed += 1
            if renamed % 200 == 0:
                print(f"  ... {renamed} renamed")
        except OSError as e:
            print(f"  ERROR: {old.name} -> {new.name}: {e}", file=sys.stderr)
            errors += 1

    # Write changelog
    changelog = {
        "timestamp": timestamp,
        "library_root": str(root),
        "total_renames": renamed,
        "renames": changelog_entries,
    }
    changelog_path.write_text(
        json.dumps(changelog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"\nDone: renamed {renamed}, skipped {skipped}, errors {errors}.")
    print(f"Changelog: {changelog_path}")
    print(f"To rollback: python3 {__file__} {root} --rollback {changelog_path}")


if __name__ == "__main__":
    main()
