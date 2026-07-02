#!/usr/bin/env python3
"""
Push metadata from audio tags directly to Audiobookshelf via API.

Reads audio tags with mutagen, parses folder names and TXT/NFO files,
then patches each ABS library item via the API.

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
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import metadata building from our existing generator
sys.path.insert(0, os.path.dirname(__file__))
try:
    from abs_generate_metadata import build_metadata, to_abs_metadata
except ImportError:
    sys.path.insert(0, "/tmp")
    from abs_generate_metadata import build_metadata, to_abs_metadata

BATCH_SIZE = 50
RATE_LIMIT_RPS = 10

# ABS mounts /volume3/eBOOKs as /audiobooks
ABS_ROOT = "/audiobooks"
LOCAL_ROOT = "/volume3/eBOOKs"


def get_session() -> tuple[str, requests.Session]:
    base = os.environ.get("ABS_URL", "").rstrip("/")
    key = os.environ.get("ABS_API_KEY", "")
    if not base or not key:
        print("ERROR: Set ABS_URL and ABS_API_KEY.", file=sys.stderr)
        sys.exit(1)
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    return base, s


def list_libraries(base: str, s: requests.Session) -> list[dict]:
    r = s.get(f"{base}/api/libraries", timeout=10)
    r.raise_for_status()
    return r.json().get("libraries", [])


def list_all_items(base: str, s: requests.Session, library_id: str) -> list[dict]:
    items: list[dict] = []
    page = 0
    while True:
        r = s.get(
            f"{base}/api/libraries/{library_id}/items",
            params={"limit": BATCH_SIZE, "page": page, "expanded": 1},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("results", []))
        if len(items) >= data.get("total", 0):
            break
        page += 1
    return items


def build_patch(abs_item: dict, local_meta: dict, force: bool = False) -> dict | None:
    """Compare ABS metadata with local metadata, return API patch if needed."""
    current = abs_item.get("media", {}).get("metadata", {})
    patch: dict = {}

    # Title
    local_title = local_meta.get("title", "")
    current_title = current.get("title", "")
    if local_title and (force or not current_title or current_title.endswith((".mp3", ".m4a", ".m4b"))):
        if local_title != current_title:
            patch["title"] = local_title

    # Narrators
    local_narr = local_meta.get("narrators", [])
    current_narr = current.get("narrators") or []
    if local_narr and (force or not current_narr):
        if set(local_narr) != set(current_narr):
            patch["narrators"] = local_narr

    # Genres
    local_genres = local_meta.get("genres", [])
    current_genres = current.get("genres") or []
    if local_genres and (force or not current_genres):
        if set(local_genres) != set(current_genres):
            patch["genres"] = local_genres

    # Publisher
    local_pub = local_meta.get("publisher", "")
    if local_pub and (force or not current.get("publisher")):
        if local_pub != current.get("publisher"):
            patch["publisher"] = local_pub

    # Published year
    local_year = local_meta.get("publishedYear", "")
    if local_year and (force or not current.get("publishedYear")):
        if local_year != current.get("publishedYear"):
            patch["publishedYear"] = local_year

    # Description
    local_desc = local_meta.get("description", "")
    if local_desc and len(local_desc) > 100 and (force or not current.get("description")):
        patch["description"] = local_desc

    if not patch:
        return None
    return {"metadata": patch}


def main() -> None:
    parser = argparse.ArgumentParser(description="Push metadata to ABS via API")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--library", help="Only process this library name")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite all fields, not just empty ones")
    args = parser.parse_args()

    base, session = get_session()
    libraries = list_libraries(base, session)

    if args.library:
        libraries = [lib for lib in libraries if lib["name"] == args.library]
        if not libraries:
            print(f"Library '{args.library}' not found.", file=sys.stderr)
            sys.exit(1)

    total_updated = 0
    total_skipped = 0
    total_not_found = 0
    total_errors = 0
    last_req = 0.0

    for lib in libraries:
        lib_name = lib["name"]
        lib_id = lib["id"]
        print(f"\n{'='*60}")
        print(f"Library: {lib_name}")
        print(f"{'='*60}")

        items = list_all_items(base, session, lib_id)
        print(f"  {len(items)} items", flush=True)

        for i, item in enumerate(items):
            item_id = item["id"]
            abs_path = item.get("path", "")

            # Map ABS container path to local NAS path
            if abs_path.startswith(ABS_ROOT):
                local_path = Path(LOCAL_ROOT) / abs_path[len(ABS_ROOT):].lstrip("/")
            else:
                local_path = Path(abs_path)

            try:
                if not local_path.is_dir():
                    total_not_found += 1
                    continue
            except PermissionError:
                total_errors += 1
                continue

            # Build local metadata
            try:
                meta = build_metadata(local_path)
                local_meta = to_abs_metadata(meta)
            except (PermissionError, OSError):
                total_errors += 1
                continue
            except Exception:
                total_errors += 1
                continue

            # Compare and build patch
            patch = build_patch(item, local_meta, args.force)
            if not patch:
                total_skipped += 1
                continue

            changes = []
            for k, v in patch["metadata"].items():
                if k == "description":
                    changes.append("description=...")
                elif isinstance(v, list):
                    changes.append(f"{k}={v}")
                else:
                    changes.append(f"{k}={str(v)[:40]}")
            desc = "; ".join(changes)

            if args.dry_run:
                current_title = item.get("media", {}).get("metadata", {}).get("title", "?")
                print(f"  [{i+1}] {current_title[:50]}")
                print(f"       -> {desc[:100]}")
                total_updated += 1
                continue

            # Rate limit
            elapsed = time.time() - last_req
            if elapsed < 1.0 / RATE_LIMIT_RPS:
                time.sleep(1.0 / RATE_LIMIT_RPS - elapsed)

            try:
                r = session.patch(
                    f"{base}/api/items/{item_id}/media",
                    json=patch,
                    timeout=10,
                )
                r.raise_for_status()
                last_req = time.time()
                total_updated += 1
                if total_updated % 100 == 0:
                    print(f"  ... {total_updated} updated ({i+1}/{len(items)})", flush=True)
            except requests.RequestException as e:
                print(f"  ERROR: {item_id} — {e}", file=sys.stderr)
                total_errors += 1

    action = "Would update" if args.dry_run else "Updated"
    print(f"\nDone: {action} {total_updated}, skipped {total_skipped}, "
          f"not found {total_not_found}, errors {total_errors}.")


if __name__ == "__main__":
    main()
