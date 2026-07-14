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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audiobiblio.core.db.session import get_session
from audiobiblio.core.db.models import CdwifiDownload
import cdwifi_manifest as manifest_mod

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


def is_downloaded(source: str, title: str, track_number: int = None,
                  track_title: str = None) -> bool:
    """Check if this file was already downloaded (in DB).

    Matches on source + title + track_number — these are stable across
    trains, unlike source_url which varies per vehicle.

    Video files carry NO track numbers — without a further discriminator the
    first completed file of a movie marked every OTHER file of that movie as
    "already downloaded" (multi-file movies silently skipped). When
    track_number is missing, track_title identifies the file instead.
    """
    db = get_db()
    q = db.query(CdwifiDownload).filter_by(
        source=source, title=title, status="complete"
    )
    if track_number is not None:
        q = q.filter_by(track_number=track_number)
    elif track_title is not None:
        q = q.filter_by(track_title=track_title)
    return q.first() is not None


def _tag_genre_cdcz(file_path: str) -> None:
    """Append genre 'cd.cz' to the file's tags (user rule: every file that
    came from the train portal carries genre cd.cz). Best-effort — tagging
    must never break the download/reconcile flow."""
    try:
        p = Path(file_path)
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            try:
                t = EasyID3(str(p))
            except ID3NoHeaderError:
                from mutagen.mp3 import MP3
                m = MP3(str(p)); m.add_tags(); m.save()
                t = EasyID3(str(p))
            g = t.get("genre", [])
            if not any("cd.cz" in x for x in g):
                t["genre"] = g + ["cd.cz"] if g else ["cd.cz"]
                t.save()
        elif ext in (".m4a", ".m4b"):
            from mutagen.mp4 import MP4
            t = MP4(str(p))
            g = t.tags.get("\xa9gen", []) if t.tags else []
            if not any("cd.cz" in str(x) for x in g):
                t["\xa9gen"] = list(g) + ["cd.cz"] if g else ["cd.cz"]
                t.save()
    except Exception:
        pass


def record_download(
    source: str, source_id: str, title: str, source_url: str,
    file_path: str, size_bytes: int,
    author: str = None, track_number: int = None, track_title: str = None,
    extra: dict = None,
):
    """Record a completed download in the DB. Also stamps genre 'cd.cz'."""
    _tag_genre_cdcz(file_path)
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


def safe_write_metadata(folder: Path, detail: dict) -> None:
    """Write metadata.json, tolerant of TCC/permission errors on overwrite.

    Skips silently if the existing file can't be replaced — the API detail is
    transient anyway and any prior file is good enough.
    """
    target = folder / "metadata.json"
    payload = json.dumps(detail, ensure_ascii=False, indent=2)
    try:
        target.write_text(payload)
    except PermissionError:
        # Existing file from a different process can't be overwritten under TCC.
        # Skip — what's already on disk is close enough.
        pass


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
    if source and is_downloaded(source, title, track_number, track_title):
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
    # --retry-all-errors makes curl retry mid-transfer failures (exit 18/23/etc),
    # not just connection-setup errors. The cdwifi portal regularly drops
    # mid-transfer when the train passes through tunnels or hands off cells.
    cmd = [
        "curl", ("-#kL" if sys.stdout.isatty() else "-skL"), "-C", "-",
        "--retry", "5", "--retry-delay", "2", "--retry-all-errors",
        "--fail",
        "-o", str(dest), url,
    ]
    result = subprocess.run(cmd)
    if result.returncode == 33 and dest.exists():
        # Server doesn't support byte ranges — restart from scratch.
        print("    server doesn't support resume, restarting")
        try:
            dest.unlink()
        except PermissionError:
            print(f"    cannot remove existing partial (TCC), leaving as-is")
            return False
        result = subprocess.run(
            ["curl", ("-#kL" if sys.stdout.isatty() else "-skL"),
             "--retry", "5", "--retry-delay", "2", "--retry-all-errors",
             "--fail",
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


# Live queue file (--queue-file): re-read BETWEEN books so the user can
# reprioritize a running session by editing the file — ids listed first
# (in file order), everything else keeps its position after them.
QUEUE_FILE: Path | None = None


def _apply_queue_file(remaining: list[dict]) -> list[dict]:
    if QUEUE_FILE is None or not QUEUE_FILE.exists():
        return remaining
    try:
        wanted = [
            line.strip()
            for line in QUEUE_FILE.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    except OSError:
        return remaining
    by_id = {str(x["id"]): x for x in remaining}
    first = [by_id[i] for i in wanted if i in by_id]
    rest = [x for x in remaining if str(x["id"]) not in set(wanted)]
    if first:
        print(f"  [fronta] další dle {QUEUE_FILE.name}: "
              + ", ".join(str(x['id']) for x in first[:5]))
    return first + rest


def _seed_queue_file(catalog: list[dict]) -> None:
    """Write the current order into the queue file (once) so the user can
    see and edit the actual queue instead of guessing ids."""
    if QUEUE_FILE is None or QUEUE_FILE.exists():
        return
    lines = ["# Fronta stahovani — jeden id na radek; poradi radku = priorita.",
             "# Soubor se znovu nacita pred kazdou knihou. Smazany radek = bez zmeny poradi."]
    lines += [f"{x['id']}  # {x.get('title', '?')}" for x in catalog]
    try:
        QUEUE_FILE.write_text("\n".join(lines) + "\n")
        print(f"  [fronta] zapsana do {QUEUE_FILE} — edituj pro zmenu priorit")
    except OSError as exc:
        print(f"  [fronta] nelze zapsat: {exc}")


def backup_audiobooks(output_dir: Path, dry_run: bool,
                      scan_min: int | None, scan_max: int | None,
                      plan_only: bool = False, only_id: str | None = None,
                      preloaded_catalog: list[dict] | None = None):
    print("\n=== AUDIOBOOKS ===")
    if preloaded_catalog is not None:
        catalog = preloaded_catalog
    else:
        catalog = discover_items("audiobook", "audiobook/{}", scan_min, scan_max)
    if only_id:
        catalog = [x for x in catalog if str(x["id"]) == str(only_id)]
    print(f"Found {len(catalog)} audiobooks\n")
    if plan_only:
        print_plan("audiobooks", catalog, "audiobook", "audiobook/{}")
        return

    _seed_queue_file(catalog)
    remaining = list(catalog)
    while remaining:
        remaining = _apply_queue_file(remaining)
        ab = remaining.pop(0)
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
            safe_write_metadata(folder, detail)

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
                 plan_only: bool = False, only_id: str | None = None,
                 preloaded_catalog: list[dict] | None = None):
    print("\n=== MUSIC ALBUMS ===")
    if preloaded_catalog is not None:
        catalog = preloaded_catalog
    else:
        catalog = discover_items("music/album", "music/album/{}", scan_min, scan_max)
    if only_id:
        catalog = [x for x in catalog if str(x["id"]) == str(only_id)]
    print(f"Found {len(catalog)} albums\n")
    if plan_only:
        print_plan("music", catalog, "music", "music/album/{}")
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
            safe_write_metadata(folder, detail)

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
                 plan_only: bool = False, only_id: str | None = None,
                 preloaded_catalog: list[dict] | None = None):
    print("\n=== THEATER / VIDEO ===")
    if preloaded_catalog is not None:
        catalog = preloaded_catalog
    else:
        catalog = discover_items("movie", "movie/{}", scan_min, scan_max)
    if only_id:
        catalog = [x for x in catalog if str(x["id"]) == str(only_id)]
    print(f"Found {len(catalog)} videos\n")
    if plan_only:
        print_plan("video", catalog, "video", "movie/{}")
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
            safe_write_metadata(folder, detail)

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
    parser.add_argument("--only-id",
                        help="Restrict to a single item ID (audiobook/album/movie). "
                             "Useful for resuming a specific title before others.")
    # Multi-device manifest workflow
    parser.add_argument("--manifest-out",
                        help="HEAD-probe sizes for every track and write a manifest JSON, then exit.")
    parser.add_argument("--manifest-in",
                        help="Use this manifest JSON instead of scanning the live portal.")
    parser.add_argument("--shard",
                        help="Take piece N of M (e.g. 1/3) from the ordered manifest.")
    parser.add_argument("--order", default="at-risk",
                        choices=sorted(manifest_mod.VALID_ORDERS),
                        help="Priority mode (default at-risk). User mode requires --order-file.")
    parser.add_argument("--order-file",
                        help="For --order user: file with one item ID per line in desired order.")
    parser.add_argument("--manifest-history-dir",
                        help="Directory of past manifests for rotation scoring "
                             "(default OUTPUT_DIR/manifests/).")
    parser.add_argument("--queue-file", help="Živá fronta: soubor s id knih (řádek=priorita), znovu načítaný před každou knihou; při startu se do něj zapíše aktuální pořadí")
    parser.add_argument("--no-partials-first", action="store_true",
                        help="Disable the default 'resume partials before fresh' rule.")
    parser.add_argument("--reconcile-on-disk", metavar="DIR",
                        help="Walk DIR, insert DB rows for any audiobook/music/video "
                             "files not yet recorded. Use after rsyncing from Termux/etc.")
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

    # Parse --shard once for all paths
    shard_n = shard_m = None
    if args.shard:
        try:
            n_str, m_str = args.shard.split("/")
            shard_n, shard_m = int(n_str), int(m_str)
        except Exception:
            print(f"Error: --shard must look like N/M, got {args.shard!r}", file=sys.stderr)
            sys.exit(2)

    user_ids: list[str] | None = None
    if args.order == "user":
        if not args.order_file:
            print("Error: --order user requires --order-file", file=sys.stderr)
            sys.exit(2)
        user_ids = [
            line.strip()
            for line in Path(args.order_file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

    history_dir = (
        Path(args.manifest_history_dir)
        if args.manifest_history_dir
        else output / "manifests"
    )

    # ------------------------------------------------------- reconcile
    if args.reconcile_on_disk and args.manifest_in:
        # Offline path — no portal needed (post-trip merge at home).
        run_reconcile_from_manifest(Path(args.reconcile_on_disk), args.manifest_in)
        return
    if args.reconcile_on_disk:
        run_reconcile(Path(args.reconcile_on_disk),
                      do_ab=args.all or args.audiobooks,
                      do_mu=args.all or args.music,
                      do_vi=args.all or args.video,
                      base_url=BASE_URL,
                      ab_min=ab_min, ab_max=ab_max,
                      mu_min=mu_min, mu_max=mu_max,
                      vi_min=vi_min, vi_max=vi_max)
        return

    # ------------------------------------------------------- manifest-out
    if args.manifest_out:
        run_manifest_out(args, output, history_dir,
                         shard_n, shard_m, user_ids,
                         ab_min, ab_max, mu_min, mu_max, vi_min, vi_max)
        return

    # ------------------------------------------------------- manifest-in
    preloaded: dict[str, list[dict]] = {}  # source -> catalog list
    if args.manifest_in:
        preloaded = load_manifest_for_run(
            args.manifest_in, args.order, user_ids,
            not args.no_partials_first, shard_n, shard_m,
        )

    global QUEUE_FILE
    if args.queue_file:
        QUEUE_FILE = Path(args.queue_file)

    try:
        if args.all or args.audiobooks:
            backup_audiobooks(output, args.dry_run, ab_min, ab_max,
                              args.plan_only, args.only_id,
                              preloaded.get("audiobook"))
        if args.all or args.music:
            backup_music(output, args.dry_run, mu_min, mu_max,
                         args.plan_only, args.only_id,
                         preloaded.get("music"))
        if args.all or args.video:
            backup_video(output, args.dry_run, vi_min, vi_max,
                         args.plan_only, args.only_id,
                         preloaded.get("video"))
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        print("Make sure you are connected to the CDWIFI network.", file=sys.stderr)
        sys.exit(1)

    print("\n=== BACKUP COMPLETE ===")


# ============================================================ manifest glue


def _book_to_catalog_dict(book) -> dict:
    """Convert a Manifest Book back into the API-shaped dict that backup_*() expect."""
    tracks_out: list[dict] = []
    for t in book.tracks:
        tracks_out.append({
            "trackNumber": t.num,
            "title": t.title,
            "file": t.url,
            "source": t.url,
            "duration": 0,
        })
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author or "Unknown",
        "interpreter": "Unknown",
        "duration": 0,
        "cover": book.cover_url,
        "tracks": tracks_out,
        "files": tracks_out,  # video uses 'files'
    }


def run_manifest_out(args, output: Path, history_dir: Path,
                     shard_n, shard_m, user_ids,
                     ab_min, ab_max, mu_min, mu_max, vi_min, vi_max) -> None:
    """Scan portal, HEAD-probe sizes, write manifest, exit."""
    print(f"\n=== MANIFEST-OUT → {args.manifest_out} ===")
    do_ab = args.all or args.audiobooks
    do_mu = args.all or args.music
    do_vi = args.all or args.video

    def expand_details(catalog: list[dict], detail_fmt: str,
                       tracks_key: str = "tracks") -> list[dict]:
        """Ensure every catalog item has the tracks/files key populated.

        Items from the listing endpoint only carry summary metadata; we need
        full detail to size the tracks. Scan-found items already have detail.
        """
        out: list[dict] = []
        for x in catalog:
            if tracks_key in x or "files" in x:
                out.append(x)
                continue
            try:
                out.append(api_get(detail_fmt.format(x["id"])))
            except Exception as e:
                print(f"  warn: detail fetch failed for {x['id']}: {e}")
        return out

    all_books = []
    if do_ab:
        cat = discover_items("audiobook", "audiobook/{}", ab_min, ab_max)
        if args.only_id:
            cat = [x for x in cat if str(x["id"]) == str(args.only_id)]
        cat = expand_details(cat, "audiobook/{}")
        m = manifest_mod.generate(BASE_URL, cat, "audiobook",
                                  head_size, is_downloaded)
        all_books.extend(m.books)
    if do_mu:
        cat = discover_items("music/album", "music/album/{}", mu_min, mu_max)
        if args.only_id:
            cat = [x for x in cat if str(x["id"]) == str(args.only_id)]
        cat = expand_details(cat, "music/album/{}")
        m = manifest_mod.generate(BASE_URL, cat, "music",
                                  head_size, is_downloaded)
        all_books.extend(m.books)
    if do_vi:
        cat = discover_items("movie", "movie/{}", vi_min, vi_max)
        if args.only_id:
            cat = [x for x in cat if str(x["id"]) == str(args.only_id)]
        cat = expand_details(cat, "movie/{}", tracks_key="files")
        m = manifest_mod.generate(BASE_URL, cat, "video",
                                  head_size, is_downloaded)
        all_books.extend(m.books)

    combined = manifest_mod.Manifest(
        exported_at=datetime.now(timezone.utc).isoformat(),
        base_url=BASE_URL,
        trip_id=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        books=all_books,
    )
    manifest_mod.score_rotation(combined, history_dir)
    manifest_mod.order(combined, mode=args.order, user_ids=user_ids,
                       partials_first=not args.no_partials_first)
    if shard_n is not None:
        combined = manifest_mod.shard(combined, shard_n, shard_m)

    out_path = Path(args.manifest_out)
    combined.save(out_path)

    # Always also save a history copy so subsequent rotation scoring works.
    # Use a distinct prefix (history_*) so score_rotation can ignore full manifests.
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"history_{combined.trip_id}.json"
    if not history_path.exists():
        try:
            history_path.write_text(json.dumps(
                {"books": [{"id": b.id} for b in combined.books],
                 "exported_at": combined.exported_at,
                 "trip_id": combined.trip_id},
                ensure_ascii=False))
        except PermissionError:
            pass

    print(f"Wrote {len(combined.books)} books / {combined.total_tracks} tracks "
          f"({combined.total_bytes/(1024*1024):.0f} MB total, "
          f"{combined.pending_tracks} pending) → {out_path}")


def run_reconcile_from_manifest(root: Path, manifest_path: str) -> None:
    """OFFLINE reconcile: match disk files against a trip manifest.

    The API-based run_reconcile needs the train portal — unreachable at
    home, which is exactly when you merge the phone's downloads. The trip
    manifest carries the same catalog, so it stands in. Partial files
    (size differs from the HEAD-probed expectation) are never recorded.
    """
    m = manifest_mod.Manifest.load(Path(manifest_path))
    db = get_db()
    inserted = skipped = missing = partial = 0
    subdirs = {"audiobook": "audiobooks", "music": "music", "video": "video"}

    for book in m.books:
        source = book.media
        title = book.title
        author = book.author or "Unknown"
        folder_name = (safe_filename(title) if source == "video"
                       else safe_filename(f"{author} - {title}"))
        folder = root / subdirs.get(source, source) / folder_name
        if not folder.exists():
            continue
        for t in book.tracks:
            t_title = t.title or f"Track {t.num}"
            ext = Path(t.url).suffix or ".mp3"
            dest = folder / safe_filename(f"{t.num:02d} - {t_title}{ext}")
            if not dest.exists():
                missing += 1
                continue
            if t.size is not None and dest.stat().st_size != t.size:
                partial += 1
                continue
            if is_downloaded(source, title, int(t.num), t_title):
                skipped += 1
                continue
            record_download(
                source=source, source_id=book.id, title=title,
                source_url=t.url, file_path=str(dest),
                size_bytes=dest.stat().st_size,
                author=book.author, track_number=int(t.num),
                track_title=t_title,
            )
            inserted += 1
    print(f"=== RECONCILE (manifest): {inserted} inserted, {skipped} already in DB, "
          f"{partial} partial (skipped), {missing} not on disk ===")


def run_reconcile(root: Path, *, do_ab: bool, do_mu: bool, do_vi: bool,
                  base_url: str,
                  ab_min, ab_max, mu_min, mu_max, vi_min, vi_max) -> None:
    """Walk an output directory and insert DB rows for any files not yet recorded.

    Matches files to API catalog entries by filename (safe_filename output).
    Skips already-recorded source+title+track_number combinations. Useful
    after rsyncing downloads back from a Termux/iPad/secondary worker.
    """
    print(f"\n=== RECONCILE-ON-DISK → {root} ===")
    plans: list[tuple[str, str, str]] = []
    if do_ab:
        plans.append(("audiobook", "audiobook/{}", "audiobooks"))
    if do_mu:
        plans.append(("music", "music/album/{}", "music"))
    if do_vi:
        plans.append(("video", "movie/{}", "video"))

    db = get_db()
    inserted = 0
    skipped = 0
    missing = 0
    for source, detail_fmt, subdir in plans:
        list_endpoint = "movie" if source == "video" else (
            "music/album" if source == "music" else "audiobook"
        )
        scan_min, scan_max = {
            "audiobook": (ab_min, ab_max),
            "music": (mu_min, mu_max),
            "video": (vi_min, vi_max),
        }[source]
        try:
            catalog = discover_items(list_endpoint, detail_fmt, scan_min, scan_max)
        except Exception as e:
            print(f"  {source}: cannot fetch catalog ({e}) — skipping")
            continue

        for item in catalog:
            item_id = str(item["id"])
            title = item.get("title", "?")
            author = item.get("author") or item.get("interpreter") or "Unknown"
            detail = item if ("tracks" in item or "files" in item) else api_get(
                detail_fmt.format(item_id)
            )
            tracks = detail.get("tracks") or detail.get("files") or []
            if source == "video":
                folder_name = safe_filename(title)
            else:
                folder_name = safe_filename(f"{author} - {title}")
            folder = root / subdir / folder_name
            if not folder.exists():
                continue

            for t in tracks:
                num = t.get("trackNumber", t.get("number", 0))
                t_title = t.get("title", f"Track {num}")
                ext = Path(t.get("file", "") or t.get("source", "")).suffix or ".mp3"
                fname = safe_filename(f"{num:02d} - {t_title}{ext}")
                dest = folder / fname
                if not dest.exists():
                    missing += 1
                    continue
                if is_downloaded(source, title, int(num)):
                    skipped += 1
                    continue
                record_download(
                    source=source, source_id=item_id, title=title,
                    source_url=t.get("file") or t.get("source"),
                    file_path=str(dest), size_bytes=dest.stat().st_size,
                    author=author, track_number=int(num),
                    track_title=t_title,
                )
                inserted += 1
                if inserted % 50 == 0:
                    print(f"  {inserted} inserted ...")
    print(f"=== RECONCILE: {inserted} inserted, {skipped} already in DB, "
          f"{missing} expected-but-missing-on-disk ===")


def load_manifest_for_run(path: str, order_mode: str, user_ids,
                          partials_first: bool,
                          shard_n, shard_m) -> dict[str, list[dict]]:
    """Load a manifest, apply ordering/sharding, group by media source."""
    m = manifest_mod.Manifest.load(Path(path))
    manifest_mod.order(m, mode=order_mode, user_ids=user_ids,
                       partials_first=partials_first)
    if shard_n is not None:
        m = manifest_mod.shard(m, shard_n, shard_m)
    out: dict[str, list[dict]] = {"audiobook": [], "music": [], "video": []}
    for b in m.books:
        if b.media not in out:
            continue
        out[b.media].append(_book_to_catalog_dict(b))
    print(f"Loaded manifest {path}: {len(m.books)} books, "
          f"{m.total_tracks} tracks "
          f"(audiobooks={len(out['audiobook'])} music={len(out['music'])} video={len(out['video'])})")
    return out


if __name__ == "__main__":
    main()
