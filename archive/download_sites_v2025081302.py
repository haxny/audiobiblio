#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from yt_dlp import YoutubeDL

# ========= Logging =========
logging.basicConfig(
    filename='download.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)
def _log_console(msg):
    print(msg, flush=True)
    logging.info(msg)

def _warn_console(msg):
    print(f"\x1b[33m{msg}\x1b[0m", flush=True)  # yellow
    logging.warning(msg)

def _err_console(msg):
    print(f"\x1b[31m{msg}\x1b[0m", flush=True)  # red
    logging.error(msg)

# ========= Files / DB =========
DB_FILE = 'episodes_db.json'
SERIES_FILE = 'websites_mujrozhlas.json'
MEDIA_DIR = 'media'
ARCHIVE_FILE = 'download_archive.txt'

# ========= Helpers =========
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def normalize_text(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[<>:"/\\|?*]', '', text).strip()

def get_series_dir(metadata):
    return normalize_text(
        metadata.get('album')
        or metadata.get('playlist')
        or metadata.get('title')
        or metadata.get('webpage_url_basename')
        or urlparse(metadata.get('webpage_url', '')).path.rstrip('/').split('/')[-1]
        or 'UnknownSeries'
    )

def _date_from_meta(meta):
    date = '00000000'
    rt = meta.get('release_timestamp') or meta.get('timestamp') or meta.get('upload_date')
    if rt:
        if isinstance(rt, int):
            date = time.strftime('%Y%m%d', time.localtime(rt))
        else:
            date = str(rt).replace('-', '')
    return date

def get_filename(metadata, ext):
    series = get_series_dir(metadata)
    date = _date_from_meta(metadata)
    title = normalize_text(metadata.get('title') or 'Untitled')
    return f"{series} {date} {title}{ext}"

def _list_root_infojson():
    """All info.json files directly under MEDIA_DIR."""
    if not os.path.isdir(MEDIA_DIR):
        return set()
    out = set()
    for name in os.listdir(MEDIA_DIR):
        p = os.path.join(MEDIA_DIR, name)
        if os.path.isfile(p) and name.endswith('.info.json'):
            out.add(p)
    return out

def _find_media_sibling(base_without_ext):
    for ext in ('.mp3', '.m4a', '.flac', '.wav', '.opus'):
        cand = base_without_ext + ext
        if os.path.exists(cand):
            return cand
    return None

def process_metadata(json_path, db, verbose=False):
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        _warn_console(f"[META] Failed to read {json_path}: {e}")
        return False

    episode_id = meta.get('id') or meta.get('webpage_url') or json_path
    base = json_path[:-len('.info.json')]
    media_file = _find_media_sibling(base)

    if not media_file:
        if verbose:
            _warn_console(f"[META] No media next to {json_path}")
        return False

    size = os.path.getsize(media_file)
    if episode_id in db and db[episode_id].get('size') == size:
        if verbose:
            _log_console(f"[META] Skip {episode_id}, already up-to-date.")
        return False

    # Ensure target folder
    folder = os.path.join(MEDIA_DIR, get_series_dir(meta))
    os.makedirs(folder, exist_ok=True)

    # New target path
    new_file = os.path.join(folder, get_filename(meta, os.path.splitext(media_file)[1]))

    # Avoid collisions
    stem, ext = os.path.splitext(new_file)
    counter = 1
    while os.path.exists(new_file):
        new_file = f"{stem} ({counter}){ext}"
        counter += 1

    # Move media and json
    os.rename(media_file, new_file)
    os.rename(json_path, os.path.join(folder, os.path.basename(json_path)))

    # Update DB
    db[episode_id] = {
        "series": get_series_dir(meta),
        "file": os.path.basename(new_file),
        "size": os.path.getsize(new_file),
        "last_seen": time.strftime('%Y-%m-%d')
    }
    _log_console(f"[META] Moved → {new_file}")
    return True

# ========= yt-dlp helpers =========
def _ydl(**overrides):
    base = {
        'embedmetadata': True,
        'embedinfojson': True,
        'writeinfojson': True,
        'quiet': False,
        'outtmpl': os.path.join(MEDIA_DIR, '%(title)s.%(ext)s'),
        'sleep_interval': 5,
        'max_sleep_interval': 20,
        'retries': 10,
        'download_archive': ARCHIVE_FILE,  # avoid re-download by ID
        'user_agent': 'Mozilla/5.0',
    }
    base.update(overrides)
    return YoutubeDL(base)

def _extract_entries(ydl, url):
    """Return flattened list of entries without downloading media."""
    info = ydl.extract_info(url, download=False)
    entries = []
    def walk(node):
        if isinstance(node, dict) and node.get('_type') == 'playlist' and node.get('entries'):
            for e in node['entries']:
                walk(e)
        elif isinstance(node, dict):
            entries.append(node)
    walk(info)
    if not entries and isinstance(info, dict):
        entries = [info]
    return entries

# ========= Download flows =========
def download_infojson_for_series(url, verbose=False):
    """Download only .info.json (skip media)."""
    t0 = time.time()
    try:
        ydl = _ydl(skip_download=True, writeinfojson=True)
        if verbose:
            _log_console(f"[JSON] Extracting for {url}")
        # Snapshot existing .info.json in root (we only relocate later)
        before = _list_root_infojson()
        ydl.download([url])
        after = _list_root_infojson()
        new_jsons = sorted(after - before)
        return True, new_jsons, time.time() - t0
    except KeyboardInterrupt:
        raise
    except Exception as e:
        _warn_console(f"[JSON] Failed for {url}: {e}")
        if verbose:
            traceback.print_exc()
        return False, [], time.time() - t0

def download_media_for_entries(url, db, verbose=False):
    """Download media for episodes missing in DB, based on entry extraction."""
    t0 = time.time()
    try:
        ydl = _ydl()
        if verbose:
            _log_console(f"[MEDIA] Checking entries for {url}")
        entries = _extract_entries(ydl, url)
        to_dl = []
        for e in entries:
            ep_id = e.get('id') or e.get('webpage_url') or e.get('url')
            if ep_id not in db:
                dl_url = e.get('webpage_url') or e.get('url')
                if dl_url:
                    to_dl.append(dl_url)

        if not to_dl:
            if verbose:
                _log_console("[MEDIA] Nothing new.")
            return True, [], time.time() - t0

        if verbose:
            _log_console(f"[MEDIA] Downloading {len(to_dl)} item(s)")
        # Snapshot .info.json files before downloading
        before = _list_root_infojson()
        ydl.download(to_dl)
        after = _list_root_infojson()
        new_jsons = sorted(after - before)
        return True, new_jsons, time.time() - t0
    except KeyboardInterrupt:
        raise
    except Exception as e:
        _warn_console(f"[MEDIA] Failed for {url}: {e}")
        if verbose:
            traceback.print_exc()
        return False, [], time.time() - t0

def process_json_batch(json_paths, db, verbose=False):
    """Relocate media & json into series folders and update DB."""
    moved = 0
    for jp in json_paths:
        try:
            moved |= bool(process_metadata(jp, db, verbose=verbose))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            _warn_console(f"[META] Error processing {jp}: {e}")
            if verbose:
                traceback.print_exc()
    return moved

# ========= Orchestration =========
def run_for_url(url, db, mode='parallel', verbose=False):
    """Run JSON-first then media for a single series URL, respecting mode."""
    _log_console(f"\n=== {url} ===")

    total_json_new = []
    total_media_new = []
    failures = []

    # 1) JSON phase
    ok_json, json_new, t_json = download_infojson_for_series(url, verbose=verbose)
    _log_console(f"[JSON] {url} → {'ok' if ok_json else 'fail'} ({t_json:.1f}s), new info.json: {len(json_new)}")
    total_json_new.extend(json_new)
    if not ok_json:
        failures.append(('json', url, t_json))

    # Move whatever JSON yielded
    if json_new:
        process_json_batch(json_new, db, verbose=verbose)
        save_db(db)

    # 2) MEDIA phase
    ok_media, media_json_new, t_media = download_media_for_entries(url, db, verbose=verbose)
    _log_console(f"[MEDIA] {url} → {'ok' if ok_media else 'fail'} ({t_media:.1f}s), new info.json: {len(media_json_new)}")
    total_media_new.extend(media_json_new)
    if not ok_media:
        failures.append(('media', url, t_media))

    # Move media-json sidecars
    if media_json_new:
        process_json_batch(media_json_new, db, verbose=verbose)
        save_db(db)

    return {
        'url': url,
        'json_infojson': len(total_json_new),
        'media_infojson': len(total_media_new),
        'failures': failures,
        'time_total': t_json + t_media
    }

def main():
    parser = argparse.ArgumentParser(description="MujRozhlas downloader: JSON first, then media. Parallel by default.")
    parser.add_argument('--mode', choices=['parallel', 'sequential'], default='parallel', help='Execution mode')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--workers', type=int, default=min(8, os.cpu_count() or 4), help='Parallel workers for URLs')
    args = parser.parse_args()

    os.makedirs(MEDIA_DIR, exist_ok=True)
    db = load_db()

    # Load websites list (robust to JSON array or line-based)
    websites = []
    try:
        with open(SERIES_FILE, 'r', encoding='utf-8') as f:
            try:
                websites = json.load(f)
            except json.JSONDecodeError:
                # fallback: one URL per line
                f.seek(0)
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        websites.append(line)
    except FileNotFoundError:
        _err_console(f"Missing {SERIES_FILE}. Please create it (JSON array or newline-separated).")
        sys.exit(1)

    websites = [w.strip() for w in websites if w.strip()]
    if not websites:
        _warn_console("No websites to process. Exiting.")
        return

    _log_console(f"Loaded {len(websites)} site(s). Mode: {args.mode}. Workers: {args.workers}. Verbose: {args.verbose}")

    results = []
    failed_urls = []

    t_start = time.time()
    try:
        if args.mode == 'sequential':
            for i, url in enumerate(websites, 1):
                _log_console(f"[{i}/{len(websites)}] Working on {url}")
                res = run_for_url(url, db, mode='sequential', verbose=args.verbose)
                results.append(res)
                for fkind, furl, dur in res['failures']:
                    failed_urls.append((fkind, furl, dur))
        else:
            # parallel
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = {ex.submit(run_for_url, url, db, 'parallel', args.verbose): url for url in websites}
                done = 0
                total = len(futures)
                for fut in as_completed(futures):
                    done += 1
                    url = futures[fut]
                    try:
                        res = fut.result()
                        results.append(res)
                        for fkind, furl, dur in res['failures']:
                            failed_urls.append((fkind, furl, dur))
                        _log_console(f"[{done}/{total}] Finished {url} in {res['time_total']:.1f}s")
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        _warn_console(f"[FAIL] {url}: {e}")
                        if args.verbose:
                            traceback.print_exc()
                        failed_urls.append(('exception', url, 0.0))
                    finally:
                        # Persist DB regularly in parallel mode
                        save_db(db)
    except KeyboardInterrupt:
        _warn_console("Interrupted by user. Saving DB...")
        save_db(db)
        raise

    # Final DB persist
    save_db(db)

    t_total = time.time() - t_start

    # Summary
    total_jsons = sum(r['json_infojson'] for r in results)
    total_media_jsons = sum(r['media_infojson'] for r in results)
    _log_console("\n=== Summary ===")
    _log_console(f"  Sites processed: {len(results)}/{len(websites)}")
    _log_console(f"  New .info.json from JSON phase : {total_jsons}")
    _log_console(f"  New .info.json from MEDIA phase: {total_media_jsons}")
    _log_console(f"  Total time: {t_total:.1f}s")

    if failed_urls:
        avg = sum(d for _,_,d in failed_urls if d) / max(1, sum(1 for _,_,d in failed_urls if d))
        failed_list = ', '.join({u for _, u, _ in failed_urls})
        _warn_console(f"\nFailures: {len(failed_urls)} (avg {avg:.1f}s) → {failed_list}")
        ans = input("Retry failed now? [y/N]: ").strip().lower()
        if ans == 'y':
            # Retry once, sequentially, to be gentle
            _log_console("Retrying failed URLs once...")
            for kind, url, _ in failed_urls:
                try:
                    res = run_for_url(url, db, mode='sequential', verbose=args.verbose)
                    _log_console(f"[RETRY] {url} → ok (time {res['time_total']:.1f}s, remaining fails: {len(res['failures'])})")
                except Exception as e:
                    _warn_console(f"[RETRY FAIL] {url}: {e}")
            save_db(db)
        else:
            _log_console("You can retry on next run; failures are logged in download.log")

if __name__ == '__main__':
    main()
