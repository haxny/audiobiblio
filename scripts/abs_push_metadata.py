"""
Push metadata from audio tags directly to Audiobookshelf via API.

Reads audio tags with mutagen, parses folder names and TXT/NFO files,
then patches each ABS library item via the API.

Core loop lives in audiobiblio.library.abs.push_missing_metadata; this script
adds the CLI, NAS path mapping, and the local-metadata resolver that reads
mutagen tags from the NAS filesystem.

Note: build_metadata / to_abs_metadata are NOT in the audiobiblio package
because they read NAS mount paths (LOCAL_ROOT) that are deployment-specific.
They live in the sibling abs_generate_metadata.py.

Usage:
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python3 scripts/abs_push_metadata.py --dry-run
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python3 scripts/abs_push_metadata.py
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python3 scripts/abs_push_metadata.py --library Fiction
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python3 scripts/abs_push_metadata.py --library Fiction --force
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import metadata building from our existing generator (NAS-specific).
# Kept here (not in audiobiblio.library.abs) because build_metadata reads
# LOCAL_ROOT filesystem paths that are deployment-specific.
sys.path.insert(0, os.path.dirname(__file__))
try:
    from abs_generate_metadata import build_metadata, to_abs_metadata
except ImportError:
    sys.path.insert(0, "/tmp")
    from abs_generate_metadata import build_metadata, to_abs_metadata

from audiobiblio.library.abs import AbsClient, push_missing_metadata

# ABS mounts /volume3/eBOOKs as /audiobooks
ABS_ROOT = "/audiobooks"
LOCAL_ROOT = "/volume3/eBOOKs"


def _make_local_metadata_fn(force: bool) -> object:
    """Return a local_metadata_fn closure for push_missing_metadata.

    Maps ABS container paths (ABS_ROOT) to NAS local paths (LOCAL_ROOT),
    reads mutagen tags, and returns a metadata dict or None when the path
    doesn't exist or can't be read.
    """

    def local_metadata_fn(item: dict) -> dict | None:
        abs_path = item.get("path", "")
        if abs_path.startswith(ABS_ROOT):
            local_path = Path(LOCAL_ROOT) / abs_path[len(ABS_ROOT):].lstrip("/")
        else:
            local_path = Path(abs_path)

        try:
            if not local_path.is_dir():
                return None
        except PermissionError:
            return None

        try:
            meta = build_metadata(local_path)
            return to_abs_metadata(meta)
        except (PermissionError, OSError):
            return None
        except Exception:
            return None

    return local_metadata_fn


def main() -> None:
    parser = argparse.ArgumentParser(description="Push metadata to ABS via API")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--library", help="Only process this library name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite all fields, not just empty ones",
    )
    args = parser.parse_args()

    client = AbsClient.from_config()
    if not client.base_url:
        print("ERROR: Set ABS_URL and ABS_API_KEY.", file=sys.stderr)
        sys.exit(1)

    libraries = client.get_libraries()
    if args.library:
        libraries = [lib for lib in libraries if lib["name"] == args.library]
        if not libraries:
            print(f"Library '{args.library}' not found.", file=sys.stderr)
            sys.exit(1)

    local_metadata_fn = _make_local_metadata_fn(args.force)
    total_updated = 0
    total_skipped = 0
    total_no_meta = 0
    total_errors = 0

    for lib in libraries:
        lib_name = lib["name"]
        lib_id = lib["id"]
        print(f"\n{'='*60}")
        print(f"Library: {lib_name}")
        print(f"{'='*60}")

        stats = push_missing_metadata(
            client,
            lib_id,
            local_metadata_fn,
            dry_run=args.dry_run,
            force=args.force,
        )
        total_updated += stats["updated"]
        total_skipped += stats["skipped"]
        total_no_meta += stats["no_meta"]
        total_errors += stats["errors"]
        print(
            f"  updated={stats['updated']} skipped={stats['skipped']} "
            f"no_meta={stats['no_meta']} errors={stats['errors']}",
            flush=True,
        )

    action = "Would update" if args.dry_run else "Updated"
    print(
        f"\nDone: {action} {total_updated}, skipped {total_skipped}, "
        f"not found {total_no_meta}, errors {total_errors}."
    )


if __name__ == "__main__":
    main()
