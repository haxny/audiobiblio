#!/usr/bin/env python3
"""Standalone CDWIFI manifest downloader for Termux / minimal environments.

Single file, stdlib only + `curl` binary. No SQLAlchemy, no audiobiblio
package — just enough to download tracks listed in a manifest produced
by cdwifi_backup.py --manifest-out.

Usage (on Termux):
    pkg install python curl
    # copy manifest.json to the device, then:
    python3 cdwifi_termux_dl.py --manifest manifest.json \
        --output ~/storage/downloads/cdwifi \
        --shard 2/3        # this device handles shard 2 of 3

After the trip, rsync the output dir back to the Mac so reconciliation
can register the downloads in the central DB:
    rsync -av ~/storage/downloads/cdwifi/ MAC:~/Downloads/audiobiblio/cd.cz/
    # then on Mac:
    python3 scripts/cdwifi_backup.py --reconcile-on-disk \
        ~/Downloads/audiobiblio/cd.cz
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from unicodedata import category, normalize


def safe_filename(name: str) -> str:
    """Strip diacritics and sanitize for filesystem. Mirrors cdwifi_backup.py."""
    nfkd = normalize("NFKD", name)
    s = "".join(c for c in nfkd if category(c) != "Mn")
    for ch in r'<>:"/\|?*':
        s = s.replace(ch, "_")
    return s.strip().rstrip(".")


def media_subdir(media: str) -> str:
    return {"audiobook": "audiobooks", "music": "music", "video": "video"}.get(media, media)


def folder_for(book: dict) -> str:
    if book["media"] == "video":
        return safe_filename(book["title"])
    author = book.get("author") or "Unknown"
    return safe_filename(f"{author} - {book['title']}")


def track_filename(track: dict) -> str:
    num = int(track["num"])
    title = track["title"] or f"Track {num}"
    # Extension comes from URL
    suffix = Path(track["url"]).suffix or ".mp3"
    return safe_filename(f"{num:02d} - {title}{suffix}")


def is_complete(dest: Path, expected_size: int | None) -> bool:
    """File is 'done' if a .done marker exists OR size matches HEAD-probed expected."""
    if not dest.exists():
        return False
    if (dest.parent / (dest.name + ".done")).exists():
        return True
    if expected_size is not None and dest.stat().st_size == expected_size:
        return True
    return False


def download_one(base_url: str, track: dict, dest: Path) -> tuple[bool, str]:
    """Run curl with resume + retry; return (ok, message)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    pre = dest.stat().st_size if dest.exists() else 0
    url = base_url.rstrip("/") + track["url"]
    cmd = [
        "curl", "-#kL", "-C", "-",
        "--retry", "5", "--retry-delay", "2", "--retry-all-errors",
        "--fail",
        "-o", str(dest), url,
    ]
    rc = subprocess.run(cmd).returncode
    if rc == 33 and dest.exists():
        # Server doesn't support byte-range resume
        try:
            dest.unlink()
        except OSError:
            return False, f"cannot delete partial after exit 33"
        rc = subprocess.run(
            ["curl", "-#kL",
             "--retry", "5", "--retry-delay", "2", "--retry-all-errors",
             "--fail", "-o", str(dest), url]
        ).returncode
    if rc != 0:
        return False, f"curl exit {rc} (partial kept at {dest})"
    now = dest.stat().st_size
    delta_mb = (now - pre) / (1024 * 1024)
    # Mark complete
    try:
        (dest.parent / (dest.name + ".done")).write_text("")
    except Exception:
        pass
    return True, f"{now/(1024*1024):.1f} MB total, +{delta_mb:.1f} MB this run"


def parse_shard(s: str | None) -> tuple[int, int] | tuple[None, None]:
    if not s:
        return None, None
    a, b = s.split("/")
    return int(a), int(b)


def main() -> None:
    p = argparse.ArgumentParser(description="Termux/minimal-env CDWIFI downloader")
    p.add_argument("--manifest", required=True, help="Manifest JSON path")
    p.add_argument("--output", required=True, help="Output base directory")
    p.add_argument("--shard", help="Take piece N of M (e.g. 2/3) of the BOOK list")
    p.add_argument("--base-url", help="Override portal base URL (default = manifest's)")
    args = p.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    books = manifest["books"]
    base_url = args.base_url or manifest["base_url"]

    shard_n, shard_m = parse_shard(args.shard)
    if shard_n is not None:
        books = [b for i, b in enumerate(books) if i % shard_m == (shard_n - 1)]
        print(f"Shard {shard_n}/{shard_m}: {len(books)} books")

    output = Path(args.output)
    done_tracks = 0
    err_tracks = 0
    total_bytes_this_run = 0

    for book in books:
        media = book["media"]
        folder = output / media_subdir(media) / folder_for(book)
        print(f"\n[{book['id']}] {book['title']} — {book.get('author') or '?'}"
              f" ({len(book['tracks'])} tracks)")

        # Cover
        if book.get("cover_url"):
            suffix = Path(book["cover_url"]).suffix or ".jpg"
            cover_dest = folder / f"cover{suffix}"
            if not is_complete(cover_dest, None):
                ok, msg = download_one(
                    base_url,
                    {"url": book["cover_url"], "num": 0, "title": "cover"},
                    cover_dest,
                )
                print(f"  cover: {'OK' if ok else 'ERR'} — {msg}")

        # Tracks
        for t in book["tracks"]:
            if t.get("in_db"):
                continue  # already finished per Mac DB
            dest = folder / track_filename(t)
            if is_complete(dest, t.get("size")):
                continue
            ok, msg = download_one(base_url, t, dest)
            if ok:
                done_tracks += 1
                size = dest.stat().st_size if dest.exists() else 0
                total_bytes_this_run += size
                print(f"  [{t['num']:02d}] OK   {msg}")
            else:
                err_tracks += 1
                print(f"  [{t['num']:02d}] ERR  {msg}")

    print(f"\n=== DONE: {done_tracks} tracks downloaded, {err_tracks} errors, "
          f"{total_bytes_this_run/(1024*1024):.0f} MB this run ===")


if __name__ == "__main__":
    main()
