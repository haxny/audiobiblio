#!/usr/bin/env python3
"""
Backup audiobooks, music albums, and theater videos from CD WiFi (cdwifi.cz).

Usage:
    python scripts/cdwifi_backup.py [--base-url URL] [--output-dir DIR] [--dry-run]
                                     [--audiobooks] [--music] [--video] [--all]

Must be connected to CDWIFI network on a České dráhy train.
Downloads are tracked in the audiobiblio database (cdwifi_downloads table)
so re-runs skip already-completed files.
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unicodedata import normalize, category

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audiobiblio.db.session import get_session
from audiobiblio.db.models import CdwifiDownload

BASE_URL = "https://cdwifi.cz"
DB_SESSION = None


def _set_base_url(url: str):
    global BASE_URL
    BASE_URL = url


# Disable SSL verification (train portal uses untrusted cert)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def get_db():
    global DB_SESSION
    if DB_SESSION is None:
        DB_SESSION = get_session()
    return DB_SESSION


def is_downloaded(source: str, title: str, track_number: int = None) -> bool:
    """Check if this file was already downloaded (in DB).

    Matches on source + title + track_number — these are stable across
    trains, unlike source_url which varies per vehicle.
    """
    db = get_db()
    q = db.query(CdwifiDownload).filter_by(
        source=source, title=title, status="complete"
    )
    if track_number is not None:
        q = q.filter_by(track_number=track_number)
    return q.first() is not None


def record_download(
    source: str, source_id: str, title: str, source_url: str,
    file_path: str, size_bytes: int,
    author: str = None, track_number: int = None, track_title: str = None,
    extra: dict = None,
):
    """Record a completed download in the DB."""
    db = get_db()
    entry = CdwifiDownload(
        source=source,
        source_id=str(source_id),
        title=title,
        author=author,
        track_number=track_number,
        track_title=track_title,
        source_url=source_url,
        file_path=file_path,
        size_bytes=size_bytes,
        status="complete",
        extra=extra,
        downloaded_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.commit()


def api_get(path: str) -> list | dict:
    url = f"{BASE_URL}/portal/api/{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, context=SSL_CTX) as resp:
        return json.loads(resp.read())


def probe_detail(endpoint: str) -> dict | None:
    """Return detail JSON for an ID-bearing endpoint, or None if absent.

    The portal returns 404 for some missing IDs and 500 for others; both
    mean 'no item at this id', so we treat them as absent. Network errors
    bubble up.
    """
    try:
        return api_get(endpoint)
    except urllib.error.HTTPError as e:
        if e.code in (404, 500):
            return None
        raise


def discover_items(list_endpoint: str, detail_fmt: str,
                   scan_min: int | None, scan_max: int | None) -> list[dict]:
    """Combine API listing with numeric ID-range probing.

    The portal listing returns only ~6 items per type — many albums/movies
    are reachable only by direct ID. Returns a list of items keyed by id.
    Items from the listing keep their listing shape; probed items contain
    full detail (so backup loops can avoid a second fetch).
    """
    items: dict[str, dict] = {}
    listing = api_get(list_endpoint)
    for x in listing:
        items[str(x["id"])] = x
    if scan_min is not None and scan_max is not None:
        print(f"  Scanning {list_endpoint} IDs {scan_min}-{scan_max} ...")
        for i in range(scan_min, scan_max + 1):
            if str(i) in items:
                continue
            detail = probe_detail(detail_fmt.format(i))
            if detail and detail.get("title"):
                items[str(i)] = detail
                print(f"    found [{i}] {detail['title']}")
    return list(items.values())


def head_size(portal_path: str) -> int | None:
    """Return Content-Length for a portal path, or None if unavailable."""
    url = f"{BASE_URL}{portal_path}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as resp:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl is not None else None
    except Exception:
        return None


def _fmt_mb(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n/(1024*1024):.0f} MB"


def plan_book(detail: dict, source: str) -> tuple[int, int, int, int]:
    """HEAD every track in a book and report (n_tracks, total_bytes,
    n_done, done_bytes) where 'done' = already-complete per DB.

    Bytes are summed only for tracks we could HEAD; unknown sizes are skipped.
    """
    tracks = detail.get("tracks") or detail.get("files") or []
    title = detail.get("title", "?")
    n = len(tracks)
    total = 0
    done = 0
    done_bytes = 0
    unknown = 0
    for t in tracks:
        num = t.get("trackNumber") or t.get("number") or 0
        size = head_size(t.get("file") or t.get("source") or "")
        if size is None:
            unknown += 1
        else:
            total += size
        if is_downloaded(source, title, num):
            done += 1
            if size is not None:
                done_bytes += size
    return n, total, done, done_bytes


def print_plan(label: str, items: list[dict], source: str, detail_fmt: str) -> int:
    """Print a per-item manifest with size estimates. Returns total bytes to fetch.

    `source` is the DB key ('audiobook'|'music'|'video'); `detail_fmt` is the
    API path template (e.g. 'music/album/{}') used when an item lacks tracks.
    """
    print(f"\n--- PLAN: {label} ({len(items)} items) ---")
    grand_total = 0
    grand_remaining = 0
    for item in items:
        item_id = str(item["id"])
        title = item.get("title", "?")
        detail = item if ("tracks" in item or "files" in item) else api_get(
            detail_fmt.format(item_id)
        )
        n, total, done, done_bytes = plan_book(detail, source)
        remaining = max(total - done_bytes, 0)
        grand_total += total
        grand_remaining += remaining
        status = f"{done}/{n} done" if done else f"{n} files"
        print(f"  [{item_id}] {title}: {status}, {_fmt_mb(total)} total, {_fmt_mb(remaining)} to fetch")
    print(f"--- TOTAL: {_fmt_mb(grand_total)} catalog, {_fmt_mb(grand_remaining)} to fetch ---")
    return grand_remaining


def safe_filename(name: str) -> str:
    """Strip diacritics and sanitize for filesystem."""
    nfkd = normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if category(c) != "Mn")
    for ch in r'<>:"/\|?*':
        ascii_str = ascii_str.replace(ch, "_")
    return ascii_str.strip().rstrip(".")


def download_file(
    portal_path: str, dest: Path, dry_run: bool = False,
    *,
    source: str = None, source_id: str = None, title: str = None,
    author: str = None, track_number: int = None, track_title: str = None,
    extra: dict = None,
) -> bool:
    """Download a file. Returns True if downloaded, False if skipped."""
    # Check DB first — even if local file was moved/deleted, DB knows it's done
    if source and is_downloaded(source, title, track_number):
        print(f"  SKIP (db): {dest.name}")
        return False

    if dry_run:
        print(f"  DRY-RUN: {dest.name}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    pre_size = dest.stat().st_size if dest.exists() else 0
    if pre_size > 0:
        print(f"  Resuming: {dest.name} (have {pre_size/(1024*1024):.1f} MB)")
    else:
        print(f"  Downloading: {dest.name} ...")

    url = f"{BASE_URL}{portal_path}"
    cmd = [
        "curl", "-skL", "-C", "-",
        "--retry", "3", "--retry-delay", "2",
        "--fail",
        "-o", str(dest), url,
    ]
    result = subprocess.run(cmd)
    if result.returncode == 33 and dest.exists():
        # Server doesn't support byte ranges — restart from scratch.
        print("    server doesn't support resume, restarting")
        dest.unlink()
        result = subprocess.run(
            ["curl", "-skL", "--retry", "3", "--retry-delay", "2", "--fail",
             "-o", str(dest), url]
        )
    if result.returncode != 0:
        print(f"    ERROR: curl exit {result.returncode}, partial kept at {dest}")
        return False

    size_bytes = dest.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    delta = (size_bytes - pre_size) / (1024 * 1024)
    if pre_size > 0:
        print(f"    Done ({size_mb:.1f} MB total, +{delta:.1f} MB this run)")
    else:
        print(f"    Done ({size_mb:.1f} MB)")

    # Record in DB
    if source:
        record_download(
            source=source, source_id=source_id, title=title,
            source_url=portal_path, file_path=str(dest),
            size_bytes=size_bytes,
            author=author, track_number=track_number,
            track_title=track_title, extra=extra,
        )

    return True


def backup_audiobooks(output_dir: Path, dry_run: bool,
                      scan_min: int | None, scan_max: int | None,
                      plan_only: bool = False):
    print("\n=== AUDIOBOOKS ===")
    catalog = discover_items("audiobook", "audiobook/{}", scan_min, scan_max)
    print(f"Found {len(catalog)} audiobooks\n")
    print_plan("audiobooks", catalog, "audiobook", "audiobook/{}")
    if plan_only:
        return

    for ab in catalog:
        title = ab["title"]
        author = ab["author"]
        interpreter = ab.get("interpreter", "Unknown")
        dur_min = ab.get("duration", 0)
        h, m = divmod(dur_min, 60)
        ab_id = str(ab["id"])

        folder_name = safe_filename(f"{author} - {title}")
        folder = output_dir / "audiobooks" / folder_name
        print(f"[{ab_id}] {title} — {author} ({interpreter}), {h}h{m:02d}m")

        detail = ab if "tracks" in ab else api_get(f"audiobook/{ab_id}")
        tracks = detail.get("tracks", [])
        print(f"  {len(tracks)} tracks")

        # Save metadata
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "metadata.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2)
            )

        # Download cover
        if detail.get("cover"):
            ext = Path(detail["cover"]).suffix or ".jpg"
            download_file(
                detail["cover"], folder / f"cover{ext}", dry_run,
                source="audiobook", source_id=ab_id, title=title,
                author=author, extra={"interpreter": interpreter},
            )

        # Download tracks
        for track in tracks:
            num = track.get("trackNumber", track.get("number", 0))
            t_title = track.get("title", f"Track {num}")
            ext = Path(track["file"]).suffix or ".mp3"
            fname = safe_filename(f"{num:02d} - {t_title}{ext}")
            download_file(
                track["file"], folder / fname, dry_run,
                source="audiobook", source_id=ab_id, title=title,
                author=author, track_number=num, track_title=t_title,
                extra={"interpreter": interpreter, "duration": track.get("duration")},
            )

    print(f"\nAudiobooks done.")


def backup_music(output_dir: Path, dry_run: bool,
                 scan_min: int | None, scan_max: int | None,
                 plan_only: bool = False):
    print("\n=== MUSIC ALBUMS ===")
    catalog = discover_items("music/album", "music/album/{}", scan_min, scan_max)
    print(f"Found {len(catalog)} albums\n")
    print_plan("music", catalog, "music", "music/album/{}")
    if plan_only:
        return

    for album in catalog:
        album_id = str(album["id"])
        title = album["title"]
        artist = album.get("interpreter", "Unknown")

        folder_name = safe_filename(f"{artist} - {title}")
        folder = output_dir / "music" / folder_name
        print(f"[{album_id}] {title} — {artist}")

        detail = album if "tracks" in album else api_get(f"music/album/{album_id}")
        tracks = detail.get("tracks", [])
        print(f"  {len(tracks)} tracks")

        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "metadata.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2)
            )

        if detail.get("cover"):
            ext = Path(detail["cover"]).suffix or ".jpg"
            download_file(
                detail["cover"], folder / f"cover{ext}", dry_run,
                source="music", source_id=album_id, title=title,
                author=artist,
            )

        for track in tracks:
            num = track.get("trackNumber", 0)
            t_title = track.get("title", f"Track {num}")
            ext = Path(track["file"]).suffix or ".mp3"
            fname = safe_filename(f"{num:02d} - {t_title}{ext}")
            download_file(
                track["file"], folder / fname, dry_run,
                source="music", source_id=album_id, title=title,
                author=artist, track_number=num, track_title=t_title,
            )

    print(f"\nMusic done.")


def backup_video(output_dir: Path, dry_run: bool,
                 scan_min: int | None, scan_max: int | None,
                 plan_only: bool = False):
    print("\n=== THEATER / VIDEO ===")
    catalog = discover_items("movie", "movie/{}", scan_min, scan_max)
    print(f"Found {len(catalog)} videos\n")
    print_plan("video", catalog, "video", "movie/{}")
    if plan_only:
        return

    for movie in catalog:
        movie_id = str(movie["id"])
        title = movie["title"]
        cats = ", ".join(c["name"] for c in movie.get("categories", []))

        folder_name = safe_filename(title)
        folder = output_dir / "video" / folder_name
        print(f"[{movie_id}] {title} ({cats})")

        detail = movie if "files" in movie else api_get(f"movie/{movie_id}")
        files = detail.get("files", [])
        print(f"  {len(files)} file(s)")

        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "metadata.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2)
            )

        if detail.get("cover"):
            ext = Path(detail["cover"]).suffix or ".jpg"
            download_file(
                detail["cover"], folder / f"cover{ext}", dry_run,
                source="video", source_id=movie_id, title=title,
                extra={"director": detail.get("director"), "year": detail.get("year")},
            )

        for f in files:
            fname_raw = f.get("title", f"video_{f['id']}")
            ext = Path(f["file"]).suffix or ".m4v"
            if "." in fname_raw:
                fname = safe_filename(fname_raw)
            else:
                fname = safe_filename(f"{fname_raw}{ext}")
            download_file(
                f["file"], folder / fname, dry_run,
                source="video", source_id=movie_id, title=title,
                track_title=fname_raw,
                extra={"director": detail.get("director"), "year": detail.get("year")},
            )

    print(f"\nVideo done.")


def main():
    parser = argparse.ArgumentParser(description="Backup media from CD WiFi portal")
    parser.add_argument("--base-url", default=BASE_URL, help="Portal base URL")
    parser.add_argument("--output-dir", default="cdwifi_backup", help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="List content without downloading")
    parser.add_argument("--audiobooks", action="store_true", help="Backup audiobooks")
    parser.add_argument("--music", action="store_true", help="Backup music albums")
    parser.add_argument("--video", action="store_true", help="Backup theater/video")
    parser.add_argument("--all", action="store_true", help="Backup everything")
    # The list endpoints return only ~6 items each; ID ranges expose the rest.
    parser.add_argument("--scan-audiobooks", default="130:200",
                        help="ID range MIN:MAX to probe for audiobooks (default 130:200, '' to disable)")
    parser.add_argument("--scan-music", default="390:600",
                        help="ID range MIN:MAX to probe for music albums (default 390:600, '' to disable)")
    parser.add_argument("--scan-video", default="4040:4400",
                        help="ID range MIN:MAX to probe for movies (default 4040:4400, '' to disable)")
    parser.add_argument("--plan-only", action="store_true",
                        help="HEAD-probe sizes and print manifest, then exit without downloading")
    args = parser.parse_args()

    def parse_range(s: str) -> tuple[int | None, int | None]:
        if not s or s.strip() == "":
            return None, None
        a, b = s.split(":")
        return int(a), int(b)

    ab_min, ab_max = parse_range(args.scan_audiobooks)
    mu_min, mu_max = parse_range(args.scan_music)
    vi_min, vi_max = parse_range(args.scan_video)

    _set_base_url(args.base_url.rstrip("/"))
    output = Path(args.output_dir)

    if not (args.audiobooks or args.music or args.video or args.all):
        args.all = True

    print(f"CD WiFi Backup — {BASE_URL}")
    print(f"Output: {output.resolve()}")
    if args.dry_run:
        print("*** DRY RUN — no files will be downloaded ***")

    try:
        if args.all or args.audiobooks:
            backup_audiobooks(output, args.dry_run, ab_min, ab_max, args.plan_only)
        if args.all or args.music:
            backup_music(output, args.dry_run, mu_min, mu_max, args.plan_only)
        if args.all or args.video:
            backup_video(output, args.dry_run, vi_min, vi_max, args.plan_only)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        print("Make sure you are connected to the CDWIFI network.", file=sys.stderr)
        sys.exit(1)

    print("\n=== BACKUP COMPLETE ===")


if __name__ == "__main__":
    main()
