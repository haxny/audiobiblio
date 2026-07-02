"""
Sync Audiobookshelf metadata from embedded audio tags.

The ABS list endpoint doesn't return audio file tags, so this script:
1. Lists all items (lightweight) to find candidates needing fixes
2. Fetches full item details (with audio tags) only for items that need fixing
3. Patches metadata from audio tags

Usage:
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --dry-run
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --library Fiction
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_sync_metadata.py --force-title
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

BATCH_SIZE = 50
# Rate limit: max requests per second for individual item fetches
RATE_LIMIT_RPS = 10


def get_session() -> tuple[str, dict]:
    base = os.environ.get("ABS_URL", "").rstrip("/")
    key = os.environ.get("ABS_API_KEY", "")
    if not base or not key:
        print("ERROR: Set ABS_URL and ABS_API_KEY environment variables.", file=sys.stderr)
        sys.exit(1)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return base, headers


def list_libraries(base: str, headers: dict) -> list[dict]:
    r = requests.get(f"{base}/api/libraries", headers=headers, timeout=10)
    r.raise_for_status()
    return r.json().get("libraries", [])


def list_library_items(base: str, headers: dict, library_id: str) -> list[dict]:
    """Fetch all items from a library (lightweight, no audio tags)."""
    items: list[dict] = []
    page = 0
    while True:
        r = requests.get(
            f"{base}/api/libraries/{library_id}/items",
            headers=headers,
            params={"limit": BATCH_SIZE, "page": page, "expanded": 1},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        items.extend(results)
        total = data.get("total", 0)
        if len(items) >= total or not results:
            break
        page += 1
    return items


def get_item_detail(base: str, headers: dict, item_id: str) -> dict:
    """Fetch full item details including audio file tags."""
    r = requests.get(f"{base}/api/items/{item_id}", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def needs_fix(item: dict, force_title: bool = False) -> bool:
    """Check if an item needs metadata fixes based on list-level data."""
    meta = item.get("media", {}).get("metadata", {})
    title = meta.get("title", "")
    narrators = meta.get("narrators") or []
    num_audio = item.get("media", {}).get("numAudioFiles", 0)

    # Skip items with no audio files (ebook-only)
    if num_audio == 0:
        return False

    if force_title:
        return True

    # Bad title: hash.mp3, filename extension, empty
    exts = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus")
    if title.endswith(exts):
        return True
    if not title.strip():
        return True

    # Missing narrator
    if not narrators:
        return True

    return False


def extract_tags(item_detail: dict) -> dict:
    """Extract metadata from audio file tags of the full item detail."""
    media = item_detail.get("media", {})
    audio_files = media.get("audioFiles", [])
    if not audio_files:
        return {}

    tags = audio_files[0].get("metaTags", {})
    result: dict = {}

    if tags.get("tagAlbum"):
        result["title"] = tags["tagAlbum"]

    if tags.get("tagAlbumArtist"):
        result["authorName"] = tags["tagAlbumArtist"]
    elif tags.get("tagArtist"):
        result["authorName"] = tags["tagArtist"]

    # PERFORMER → narrator (ABS doesn't map this automatically)
    performer = tags.get("tagPerformer") or tags.get("tagComposer")
    if performer and performer != result.get("authorName"):
        result["narrator"] = performer

    if tags.get("tagPublisher"):
        result["publisher"] = tags["tagPublisher"]

    if tags.get("tagDate"):
        result["publishedYear"] = tags["tagDate"][:4]

    return result


def build_patch(meta: dict, tags: dict, force_title: bool = False) -> dict | None:
    """Build a PATCH payload comparing current ABS metadata with audio tags."""
    patch: dict = {}

    # Title
    tag_title = tags.get("title")
    current_title = meta.get("title", "")
    if tag_title:
        exts = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus")
        is_bad = (
            current_title.endswith(exts)
            or not current_title.strip()
            or ("/" in current_title)
            or ("\\" in current_title)
        )
        if force_title or is_bad:
            if tag_title != current_title:
                patch["title"] = tag_title

    # Narrator
    tag_narrator = tags.get("narrator")
    current_narrators = meta.get("narrators") or []
    if tag_narrator and not current_narrators:
        patch["narrators"] = [tag_narrator]

    # Publisher
    tag_publisher = tags.get("publisher")
    if tag_publisher and not meta.get("publisher"):
        patch["publisher"] = tag_publisher

    # Published year
    tag_year = tags.get("publishedYear")
    if tag_year and not meta.get("publishedYear"):
        patch["publishedYear"] = tag_year

    if not patch:
        return None
    return {"metadata": patch}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync ABS metadata from audio tags")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--library", help="Only process this library name")
    parser.add_argument("--force-title", action="store_true",
                        help="Always overwrite title from tags, not just bad ones")
    args = parser.parse_args()

    base, headers = get_session()
    libraries = list_libraries(base, headers)

    if args.library:
        libraries = [lib for lib in libraries if lib["name"] == args.library]
        if not libraries:
            print(f"Library '{args.library}' not found.", file=sys.stderr)
            sys.exit(1)

    total_updated = 0
    total_skipped = 0
    total_no_tags = 0
    total_errors = 0
    last_request = 0.0

    for lib in libraries:
        lib_name = lib["name"]
        lib_id = lib["id"]
        print(f"\n{'='*60}")
        print(f"Library: {lib_name}")
        print(f"{'='*60}")

        items = list_library_items(base, headers, lib_id)
        candidates = [it for it in items if needs_fix(it, args.force_title)]
        print(f"  {len(items)} items, {len(candidates)} need fixes")

        for i, item in enumerate(candidates):
            item_id = item["id"]
            current_title = item.get("media", {}).get("metadata", {}).get("title", "???")

            # Rate limit
            elapsed = time.time() - last_request
            if elapsed < 1.0 / RATE_LIMIT_RPS:
                time.sleep(1.0 / RATE_LIMIT_RPS - elapsed)

            # Fetch full item details with audio tags
            try:
                detail = get_item_detail(base, headers, item_id)
                last_request = time.time()
            except requests.RequestException as e:
                print(f"  ERROR fetching {current_title}: {e}", file=sys.stderr)
                total_errors += 1
                continue

            tags = extract_tags(detail)
            if not tags:
                total_no_tags += 1
                continue

            detail_meta = detail.get("media", {}).get("metadata", {})
            patch = build_patch(detail_meta, tags, args.force_title)

            if not patch:
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
                r = requests.patch(
                    f"{base}/api/items/{item_id}/media",
                    headers=headers,
                    json=patch,
                    timeout=10,
                )
                r.raise_for_status()
                print(f"  [{i+1}/{len(candidates)}] FIXED: {current_title} -> {changes}")
                total_updated += 1
            except requests.RequestException as e:
                print(f"  [{i+1}/{len(candidates)}] ERROR: {current_title} — {e}", file=sys.stderr)
                total_errors += 1

    action = "Would fix" if args.dry_run else "Fixed"
    print(f"\nDone: {action} {total_updated}, skipped {total_skipped}, "
          f"no tags {total_no_tags}, errors {total_errors}.")


if __name__ == "__main__":
    main()
