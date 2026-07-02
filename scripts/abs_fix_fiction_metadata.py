"""
Fix Fiction library metadata in Audiobookshelf.

1. Strip " [audio]" from author names
2. Fix hash/garbage titles from folder names
3. Add narrators from audio tags (requires individual item fetch)

Usage:
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_fix_fiction_metadata.py --dry-run
    ABS_URL=https://audio.book.cz ABS_API_KEY=xxx python scripts/abs_fix_fiction_metadata.py
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

import requests

BATCH_SIZE = 50
RATE_LIMIT_RPS = 10


def get_session() -> tuple[str, dict]:
    base = os.environ.get("ABS_URL", "").rstrip("/")
    key = os.environ.get("ABS_API_KEY", "")
    if not base or not key:
        print("ERROR: Set ABS_URL and ABS_API_KEY.", file=sys.stderr)
        sys.exit(1)
    return base, {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def list_all_items(base: str, headers: dict, library_id: str) -> list[dict]:
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
        if len(items) >= data.get("total", 0) or not results:
            break
        page += 1
    return items


def get_item_detail(base: str, headers: dict, item_id: str) -> dict:
    r = requests.get(f"{base}/api/items/{item_id}", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def clean_author(name: str) -> str:
    """Strip ' [audio]' suffix from author name."""
    return re.sub(r"\s*\[audio\]\s*$", "", name, flags=re.IGNORECASE)


def extract_title_from_folder(rel_path: str) -> str | None:
    """
    Extract book title from folder path.

    relPath examples:
      "Raymond Radiguet [audio]/Raymond Radiguet - (1923) Dabel v tele"
      "Kafka [audio]/Franz Kafka - Proces"
      "Some Book Title"

    Returns the book folder name (last path component).
    """
    parts = rel_path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[-1]
    return parts[0] if parts else None


def is_bad_title(title: str) -> bool:
    """Check if title looks like garbage (hash, filename, etc.)."""
    exts = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus")
    if title.endswith(exts):
        return True
    if not title.strip():
        return True
    # MD5 hash pattern: 32 hex chars + extension
    if re.match(r"^[0-9a-f]{32}\.\w+$", title):
        return True
    return False


def build_patch_from_list(item: dict) -> dict:
    """Build metadata patch from list-level data (no audio tags)."""
    meta = item.get("media", {}).get("metadata", {})
    rel_path = item.get("relPath", "")
    patch: dict = {}

    # Fix authors: strip [audio]
    authors = meta.get("authors", [])
    cleaned_authors = []
    authors_changed = False
    for a in authors:
        clean = clean_author(a.get("name", ""))
        if clean != a.get("name", ""):
            authors_changed = True
        cleaned_authors.append({"id": a.get("id"), "name": clean})
    if authors_changed:
        patch["authors"] = cleaned_authors

    # Fix bad titles from folder name
    current_title = meta.get("title", "")
    if is_bad_title(current_title):
        folder_title = extract_title_from_folder(rel_path)
        if folder_title and folder_title != current_title:
            # Strip [audio] from the displayed title (not from the folder!)
            folder_title = re.sub(r"\s*\[audio\]\s*$", "", folder_title, flags=re.IGNORECASE)
            if folder_title:
                patch["title"] = folder_title

    return patch


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix Fiction library metadata")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fix-narrators", action="store_true",
                        help="Also fetch individual items to fix narrators (slow)")
    args = parser.parse_args()

    base, headers = get_session()

    # Find Fiction library
    r = requests.get(f"{base}/api/libraries", headers=headers, timeout=10)
    r.raise_for_status()
    libs = r.json().get("libraries", [])
    fiction = next((lib for lib in libs if lib["name"] == "Fiction"), None)
    if not fiction:
        print("Fiction library not found!", file=sys.stderr)
        sys.exit(1)

    print(f"Loading Fiction library items...")
    items = list_all_items(base, headers, fiction["id"])
    print(f"  {len(items)} items loaded\n")

    updated = 0
    skipped = 0
    narrator_fixed = 0
    errors = 0
    last_req = 0.0

    for i, item in enumerate(items):
        item_id = item["id"]
        meta = item.get("media", {}).get("metadata", {})
        current_title = meta.get("title", "???")

        patch = build_patch_from_list(item)

        # Optionally fetch full detail for narrator
        if args.fix_narrators and not (meta.get("narrators") or []):
            num_audio = item.get("media", {}).get("numAudioFiles", 0)
            if num_audio > 0:
                elapsed = time.time() - last_req
                if elapsed < 1.0 / RATE_LIMIT_RPS:
                    time.sleep(1.0 / RATE_LIMIT_RPS - elapsed)
                try:
                    detail = get_item_detail(base, headers, item_id)
                    last_req = time.time()
                    af = detail.get("media", {}).get("audioFiles", [])
                    if af:
                        tags = af[0].get("metaTags", {})
                        performer = tags.get("tagPerformer") or tags.get("tagComposer")
                        # Don't set narrator = author
                        author_names = {
                            a.get("name", "") for a in meta.get("authors", [])
                        }
                        clean_names = {clean_author(n) for n in author_names}
                        if performer and performer not in author_names and performer not in clean_names:
                            patch["narrators"] = [performer]
                            narrator_fixed += 1
                except requests.RequestException:
                    pass

        if not patch:
            skipped += 1
            continue

        changes = []
        if "title" in patch:
            changes.append(f"title={patch['title'][:60]}")
        if "authors" in patch:
            names = [a["name"] for a in patch["authors"]]
            changes.append(f"authors={names}")
        if "narrators" in patch:
            changes.append(f"narrators={patch['narrators']}")
        desc = "; ".join(changes)

        if args.dry_run:
            print(f"  [{i+1}] WOULD FIX: {current_title[:50]}")
            print(f"       {desc}")
            updated += 1
            continue

        try:
            r = requests.patch(
                f"{base}/api/items/{item_id}/media",
                headers=headers,
                json={"metadata": patch},
                timeout=10,
            )
            r.raise_for_status()
            updated += 1
            if (updated % 100) == 0:
                print(f"  ... {updated} fixed so far ({i+1}/{len(items)})")
        except requests.RequestException as e:
            print(f"  ERROR: {current_title[:50]} — {e}", file=sys.stderr)
            errors += 1

    action = "Would fix" if args.dry_run else "Fixed"
    print(f"\nDone: {action} {updated} items, skipped {skipped}, "
          f"narrators added {narrator_fixed}, errors {errors}.")


if __name__ == "__main__":
    main()
