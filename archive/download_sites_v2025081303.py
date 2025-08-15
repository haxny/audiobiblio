#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, time, json, unicodedata, logging, argparse, shutil
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse
from yt_dlp import YoutubeDL

# ------------ Configurable Station Mapping ------------
STATION_MAP = {
    "radiozurnal.rozhlas.cz": "CRo1",
    "dvojka.rozhlas.cz": "CRo2",
    "vltava.rozhlas.cz": "CRo3",
    "plus.rozhlas.cz": "CRo+",
    "junior.rozhlas.cz": "CRoJun",
    "www.radiojunior.cz": "CRoJun",
    "wave.rozhlas.cz": "CRoW",
    "d-dur.rozhlas.cz": "CRoDdur",
    "jazz.rozhlas.cz": "CRoJazz",
    "pohoda.rozhlas.cz": "CRoPohoda",
    "radio.cz": "CRoInt",
    "brno.rozhlas.cz": "CRoBrno",
    "budejovice.rozhlas.cz": "CRoCB",
    "hradec.rozhlas.cz": "CRoHK",
    "vary.rozhlas.cz": "CRoKV",
    "liberec.rozhlas.cz": "CRoLib",
    "olomouc.rozhlas.cz": "CRoOL",
    "ostrava.rozhlas.cz": "CRoOV",
    "pardubice.rozhlas.cz": "CRoPard",
    "plzen.rozhlas.cz": "CRoPlz",
    "praha.rozhlas.cz": "CRoPRG",
    "region.rozhlas.cz": "CRoRegion",
    "sever.rozhlas.cz": "CRoSever",
    "vysocina.rozhlas.cz": "CRoVys",
    "zlin.rozhlas.cz": "CRoZL",
}

DB_FILE = 'episodes_db.json'
SERIES_FILE = 'websites_mujrozhlas.json'
MEDIA_DIR = 'media'
NFO_DIRNAME = '.nfo'
ORPHANS_DIR = '_orphans'
LOG_DATE = time.strftime('%Y%m%d')

HELP_TEXT = """
Usage:
  python mujrozhlas_downloader.py [options]

Modes:
  --mode parallel          Download JSON, media, and crawl in parallel (default)
  --mode sequential        Download JSON → media → crawl in sequence

Reprocessing:
  --reprocess              Process existing .info.json files instead of downloading
  --overwrite              With --reprocess, overwrite even already-correct files
  --dry-run                Preview actions without changing files

Other:
  --help                   Show this help message and exit

Examples:
  # Reprocess and preview changes
  python mujrozhlas_downloader.py --reprocess --overwrite --dry-run

  # Normal download mode
  python mujrozhlas_downloader.py
"""

# ------------ Logger Setup ------------
def setup_logger(log_path_prefix):
    log_path = f"{log_path_prefix}_{LOG_DATE}.log"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger(log_path)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(handler)
    return logger

GLOBAL_LOG = setup_logger(os.path.join(MEDIA_DIR, f"download"))

def log_event(meta, message):
    series, subfolder = get_series_and_subfolder(meta)
    GLOBAL_LOG.info(message)
    series_logger = setup_logger(os.path.join(MEDIA_DIR, series, NFO_DIRNAME, "series"))
    series_logger.info(message)
    if subfolder:
        book_logger = setup_logger(os.path.join(MEDIA_DIR, series, subfolder, NFO_DIRNAME, "book"))
        book_logger.info(message)

# ------------ Helpers ------------
def normalize_text(text):
    if not text: return ''
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[<>:"/\\|?*]', '', text).strip()

def _date_from_meta(meta):
    date = '00000000'
    release_time = meta.get('release_timestamp') or meta.get('upload_date')
    if release_time:
        if isinstance(release_time, int):
            date = time.strftime('%Y%m%d', time.localtime(release_time))
        else:
            date = str(release_time).replace('-', '')
    return date

def get_series_and_subfolder(meta):
    netloc = urlparse(meta.get('webpage_url', '')).netloc
    station_code = ''
    for domain, code in STATION_MAP.items():
        if domain in netloc:
            station_code = f" ({code})"
            break
    series = normalize_text(meta.get('playlist') or meta.get('album') or
                            urlparse(meta.get('webpage_url', '')).path.rstrip('/').split('/')[-1] or
                            'UnknownSeries') + station_code
    subfolder = None
    possible_names = [meta.get('album'), meta.get('title'), meta.get('chapter')]
    for name in possible_names:
        if name and normalize_text(name) != normalize_text(series.replace(station_code, '')):
            subfolder = normalize_text(name)
            break
    return series, subfolder

def get_filename(meta, ext):
    date = _date_from_meta(meta)
    ep_num = None
    for key in ('episode_number', 'chapter_number', 'playlist_index'):
        if meta.get(key):
            try:
                ep_num = int(meta[key])
                break
            except Exception:
                pass
    ep_title = normalize_text(meta.get('chapter') or meta.get('title') or 'Untitled')
    parts = [date]
    if ep_num:
        parts.append(f"Ep{ep_num:02d}")
    parts.append(ep_title)
    return " – ".join(parts) + ext

def update_metadata_index(meta):
    series, subfolder = get_series_and_subfolder(meta)
    folder_path = os.path.join(MEDIA_DIR, series)
    if subfolder:
        folder_path = os.path.join(folder_path, subfolder)
    nfo_dir = os.path.join(folder_path, NFO_DIRNAME)
    os.makedirs(nfo_dir, exist_ok=True)
    index_path = os.path.join(nfo_dir, 'book.json' if subfolder else 'series.json')
    episodes = []
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            episodes = json.load(f)
    episodes.append(meta)
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(episodes, f, indent=2, ensure_ascii=False)

# ------------ DB Load/Save ------------
def load_db():
    if os.path.exists(DB_FILE):
        return json.load(open(DB_FILE, 'r', encoding='utf-8'))
    return {}

def save_db(db):
    json.dump(db, open(DB_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

# ------------ Processing ------------
def process_metadata(json_path, db, overwrite=False, dry_run=False):
    meta = json.load(open(json_path, 'r', encoding='utf-8'))
    episode_id = meta['id']
    base_name = json_path.rsplit('.info.json', 1)[0]
    media_file = None
    for ext in ['.mp3', '.m4a', '.flac', '.wav', '.opus']:
        if os.path.exists(base_name + ext):
            media_file = base_name + ext
            break
    if not media_file:
        GLOBAL_LOG.warning(f"No media for {json_path}")
        return
    size = os.path.getsize(media_file)
    if episode_id in db and db[episode_id]['size'] == size and not overwrite:
        GLOBAL_LOG.info(f"Skipping {episode_id}, already up-to-date.")
        return
    series, subfolder = get_series_and_subfolder(meta)
    folder = os.path.join(MEDIA_DIR, series, subfolder) if subfolder else os.path.join(MEDIA_DIR, series)
    os.makedirs(folder, exist_ok=True)
    new_file = os.path.join(folder, get_filename(meta, os.path.splitext(media_file)[1]))
    if dry_run:
        print(f"[DRY-RUN] Would move {media_file} -> {new_file}")
        return
    shutil.move(media_file, new_file)
    shutil.move(json_path, os.path.join(folder, os.path.basename(json_path)))
    db[episode_id] = {
        "series": series,
        "file": os.path.basename(new_file),
        "size": size,
        "last_seen": time.strftime('%Y-%m-%d')
    }
    log_event(meta, f"Moved to {new_file}")
    update_metadata_index(meta)

def find_orphans(dry_run=False):
    orphans_found = []
    for root, dirs, files in os.walk(MEDIA_DIR):
        if NFO_DIRNAME in root or ORPHANS_DIR in root:
            continue
        for f in files:
            if f.lower().endswith(('.mp3', '.m4a', '.flac', '.wav', '.opus')):
                json_name = f.rsplit('.', 1)[0] + '.info.json'
                if json_name not in files:
                    rel_path = os.path.relpath(os.path.join(root, f), MEDIA_DIR)
                    orphans_found.append(rel_path)
    if orphans_found:
        orphan_target_root = os.path.join(MEDIA_DIR, ORPHANS_DIR, LOG_DATE)
        for rel_path in orphans_found:
            src_path = os.path.join(MEDIA_DIR, rel_path)
            dest_path = os.path.join(orphan_target_root, rel_path)
            if dry_run:
                print(f"[DRY-RUN] Would move orphan {src_path} -> {dest_path}")
            else:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.move(src_path, dest_path)
        with open(os.path.join(orphan_target_root, f"orphans_{LOG_DATE}.log"), 'w', encoding='utf-8') as logf:
            for path in orphans_found:
                logf.write(path + "\n")
    return orphans_found

# ------------ Download ------------
def download_series(url, db, ydl):
    GLOBAL_LOG.info(f"Downloading from {url}")
    ydl.download([url])
    for f in os.listdir(MEDIA_DIR):
        if f.endswith('.info.json') and os.path.dirname(f) == MEDIA_DIR:
            process_metadata(os.path.join(MEDIA_DIR, f), db)

# ------------ Main ------------
def main():
    parser = argparse.ArgumentParser(description="MujRozhlas downloader & reprocessor", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--mode', choices=['parallel', 'sequential'], default='parallel', help="Download mode")
    parser.add_argument('--reprocess', action='store_true', help="Process existing .info.json files instead of downloading")
    parser.add_argument('--overwrite', action='store_true', help="With --reprocess, overwrite even already-correct files")
    parser.add_argument('--dry-run', action='store_true', help="Preview actions without changing files")
    parser.add_argument('--help-text', action='store_true', help="Show extended help text")
    args = parser.parse_args()

    if args.help_text:
        print(HELP_TEXT)
        with open('help.txt', 'w', encoding='utf-8') as f:
            f.write(HELP_TEXT)
        return

    db = load_db()

    if args.reprocess:
        for root, dirs, files in os.walk(MEDIA_DIR):
            for f in files:
                if f.endswith('.info.json'):
                    process_metadata(os.path.join(root, f), db, overwrite=args.overwrite, dry_run=args.dry_run)
        orphans = find_orphans(dry_run=args.dry_run)
        GLOBAL_LOG.info(f"Orphans found: {len(orphans)}")
    else:
        ydl_opts = {
            'embedmetadata': True,
            'embedinfojson': True,
            'writeinfojson': True,
            'quiet': False,
            'outtmpl': f'{MEDIA_DIR}/%(title)s.%(ext)s',
            'sleep_interval': 15,
            'max_sleep_interval': 157,
            'retries': 10
        }
        ydl = YoutubeDL(ydl_opts)
        websites = json.load(open(SERIES_FILE, 'r', encoding='utf-8'))
        for url in websites:
            download_series(url, db, ydl)
        orphans = find_orphans()
        GLOBAL_LOG.info(f"Orphans found: {len(orphans)}")

    save_db(db)

if __name__ == '__main__':
    main()
