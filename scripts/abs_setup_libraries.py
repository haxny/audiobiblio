"""
Create Audiobookshelf libraries for all audio content directories.

Usage:
    ABS_URL=http://localhost:13378 ABS_API_KEY=xxx python scripts/abs_setup_libraries.py

    --dry-run   Print what would be created without making API calls.
    --delete    Delete ALL existing libraries first (use with caution).
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

ABS_ROOT = "/audiobooks"  # mount point inside ABS container


def abs_path(*parts: str) -> str:
    return f"{ABS_ROOT}/{'/'.join(parts)}"


# ── Library definitions ─────────────────────────────────────────────

LIBRARIES: list[dict] = [
    # --- Top-level ---
    {"name": "Fiction", "path": abs_path("eBOOKs.fiction"), "audio_only": True},
    {"name": "4kids", "path": abs_path("4kids"), "audio_only": True},
    {"name": "mujrozhlas", "path": abs_path("mujrozhlas"), "audio_only": True},
    # --- Working / unsorted ---
    {"name": "2sort", "path": abs_path("2sort"), "audio_only": True},
    {"name": "Downloads", "path": abs_path("eBOOKs.downloads"), "audio_only": True},
    {"name": "Incomplete", "path": abs_path("eBOOKs.INCOMPLETE"), "audio_only": True},
    {"name": "Temp", "path": abs_path("eBOOKs.temp"), "audio_only": True},
    {"name": "Temp 2sort", "path": abs_path("eBOOKs.temp2sort"), "audio_only": True},
    {"name": "Temp 2sort ZV", "path": abs_path("eBOOKs.temp2sort.ZV"), "audio_only": True},
    {"name": "Temp 2sort 2025", "path": abs_path("eBOOKs.temp2sort2025"), "audio_only": True},
    {
        "name": "Zl\u00edn",
        "path": abs_path("eBOOKs.Zl\u00edn"),
        "audio_only": True,
    },
    # --- Languages (single library) ---
    {"name": "Languages", "path": abs_path("eBOOKs.languages"), "audio_only": True},
    # --- Nonfiction [audio] per subject ---
    *[
        {
            "name": f"NF: {subject}",
            "path": abs_path("eBOOKs.nonfiction", f"{subject} [audio]"),
            "audio_only": True,
        }
        for subject in [
            "architecture",
            "assorted science",
            "biography",
            "biology",
            "blogs & podcasts",
            "culture",
            "economics",
            "esoterics, religion",
            "finance",
            "food, cuisine",
            "history",
            "investigative journalism",
            "law",
            "linguistics",
            "literature",
            "mathematics",
            "medical",
            "music",
            "parenting",
            "philosophy",
            "physics",
            "politology",
            "psychology",
            "science",
            "self-improvement",
            "skills training",
            "sociology",
            "travels",
            "xxx",
        ]
    ],
]


# ── API helpers ──────────────────────────────────────────────────────


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


def delete_library(base: str, headers: dict, lib_id: str, name: str) -> None:
    r = requests.delete(f"{base}/api/libraries/{lib_id}", headers=headers, timeout=10)
    r.raise_for_status()
    print(f"  DELETED: {name} ({lib_id})")


def create_library(base: str, headers: dict, lib: dict) -> dict:
    body = {
        "name": lib["name"],
        "folders": [{"path": lib["path"]}],
        "mediaType": "book",
        "provider": "google",
        "settings": {
            "audiobooksOnly": lib.get("audio_only", True),
        },
    }
    r = requests.post(f"{base}/api/libraries", headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


# ── Main ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup ABS libraries")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without API calls")
    parser.add_argument("--delete", action="store_true", help="Delete all existing libraries first")
    args = parser.parse_args()

    if args.dry_run:
        print(f"Would create {len(LIBRARIES)} libraries:\n")
        for lib in LIBRARIES:
            print(f"  {lib['name']:30s} -> {lib['path']}")
        return

    base, headers = get_session()

    # Optionally delete existing libraries
    if args.delete:
        existing = list_libraries(base, headers)
        if existing:
            print(f"Deleting {len(existing)} existing libraries...")
            for lib in existing:
                delete_library(base, headers, lib["id"], lib["name"])
            print()

    # Check existing library names/paths to skip duplicates
    existing = list_libraries(base, headers)
    existing_paths = set()
    for lib in existing:
        for folder in lib.get("folders", []):
            existing_paths.add(folder.get("fullPath", folder.get("path", "")))

    # Create libraries
    created = 0
    skipped = 0
    failed = 0

    print(f"Creating {len(LIBRARIES)} libraries...\n")
    for lib in LIBRARIES:
        if lib["path"] in existing_paths:
            print(f"  SKIP (exists): {lib['name']}")
            skipped += 1
            continue
        try:
            result = create_library(base, headers, lib)
            lib_id = result.get("id", "?")
            print(f"  CREATED: {lib['name']} ({lib_id})")
            created += 1
        except requests.RequestException as e:
            print(f"  FAILED: {lib['name']} — {e}", file=sys.stderr)
            failed += 1

    print(f"\nDone: {created} created, {skipped} skipped, {failed} failed.")


if __name__ == "__main__":
    main()
