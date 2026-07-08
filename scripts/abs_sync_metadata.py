"""
Sync Audiobookshelf metadata from embedded audio tags.

The ABS list endpoint doesn't return audio file tags, so this script:
1. Lists all items (lightweight) to find candidates needing fixes
2. Fetches full item details (with audio tags) only for items that need fixing
3. Patches metadata from audio tags

Core logic lives in audiobiblio.library.abs; this script adds the CLI,
per-item progress output, and orchestration across libraries.

Usage:
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --dry-run
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --library Fiction
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --force-title
"""
from __future__ import annotations

import argparse
import sys

import requests

from audiobiblio.library.abs import AbsClient, build_patch_for_item, needs_fix


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ABS metadata from audio tags")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--library", help="Only process this library name")
    parser.add_argument(
        "--force-title",
        action="store_true",
        help="Always overwrite title from tags, not just bad ones",
    )
    args = parser.parse_args()

    client = AbsClient.from_config()
    if not client.base_url:
        print("ERROR: Set ABS_URL and ABS_API_KEY environment variables.", file=sys.stderr)
        sys.exit(1)

    libraries = client.get_libraries()
    if args.library:
        libraries = [lib for lib in libraries if lib["name"] == args.library]
        if not libraries:
            print(f"Library '{args.library}' not found.", file=sys.stderr)
            sys.exit(1)

    total_updated = 0
    total_skipped = 0
    total_no_tags = 0
    total_errors = 0

    for lib in libraries:
        lib_name = lib["name"]
        lib_id = lib["id"]
        print(f"\n{'='*60}")
        print(f"Library: {lib_name}")
        print(f"{'='*60}")

        items = client.get_library_items(lib_id)
        candidates = [it for it in items if needs_fix(it, args.force_title)]
        print(f"  {len(items)} items, {len(candidates)} need fixes")

        for i, item in enumerate(candidates):
            item_id = item["id"]
            current_title = item.get("media", {}).get("metadata", {}).get("title", "???")

            try:
                detail = client.get_item(item_id)
            except requests.RequestException as e:
                print(f"  ERROR fetching {current_title}: {e}", file=sys.stderr)
                total_errors += 1
                continue

            patch, reason = build_patch_for_item(detail, args.force_title)
            if reason == "no_tags":
                total_no_tags += 1
                continue
            if reason == "no_change":
                total_skipped += 1
                continue

            changes_parts = []
            for k, v in patch["metadata"].items():
                val = str(v) if not isinstance(v, list) else ", ".join(v)
                changes_parts.append(f"{k}={val}")
            changes = "; ".join(changes_parts)

            if args.dry_run:
                print(f"  [{i+1}/{len(candidates)}] WOULD FIX: {current_title}")
                print(f"       -> {changes}")
                total_updated += 1
                continue

            try:
                client.patch_item_media(item_id, patch)
                print(f"  [{i+1}/{len(candidates)}] FIXED: {current_title} -> {changes}")
                total_updated += 1
            except requests.RequestException as e:
                print(
                    f"  [{i+1}/{len(candidates)}] ERROR: {current_title} — {e}",
                    file=sys.stderr,
                )
                total_errors += 1

    action = "Would fix" if args.dry_run else "Fixed"
    print(
        f"\nDone: {action} {total_updated}, skipped {total_skipped}, "
        f"no tags {total_no_tags}, errors {total_errors}."
    )


if __name__ == "__main__":
    main()
