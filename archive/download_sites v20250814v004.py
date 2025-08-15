#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, time, logging, shutil

# ✅ Flexible imports: works in both direct-run and package-run mode
try:
    from .metadata import enrich_metadata
    from .utils import sanitize_filename
except ImportError:
    from metadata import enrich_metadata
    from utils import sanitize_filename

from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, TXXX
from urllib.parse import urlparse

# ✅ Always resolve paths relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'episodes_db.json')
SERIES_FILE = os.path.join(BASE_DIR, 'websites_mujrozhlas.json')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

logging.basicConfig(
    filename=os.path.join(BASE_DIR, 'download.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)
log = lambda m: (print(m), logging.info(m))

def load_db():
    if os.path.exists(DB_FILE):
        return json.load(open(DB_FILE, 'r', encoding='utf-8'))
    return {}

def save_db(db):
    json.dump(db, open(DB_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

def write_tags(file_path, tags):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            audio = EasyID3(file_path)
            for k, v in tags.items():
                if not v:
                    continue
                if k == "narrator":
                    id3 = ID3(file_path)
                    id3.add(TXXX(encoding=3, desc="Narrator", text=v))
                    id3.save(file_path)
                else:
                    audio[k] = str(v)
            audio.save()
        elif ext in [".m4a", ".mp4"]:
            audio = MP4(file_path)
            for k, v in tags.items():
                if not v:
                    continue
                if k == "title":
                    audio["\xa9nam"] = v
                elif k == "album":
                    audio["\xa9alb"] = v
                elif k == "artist":
                    audio["\xa9ART"] = v
                elif k == "albumartist":
                    audio["aART"] = v
                elif k in ["date", "originaldate"]:
                    audio["\xa9day"] = v
                elif k == "comment":
                    audio["\xa9cmt"] = v
            audio.save()
    except Exception as e:
        log(f"Tag write error for {file_path}: {e}")

def process_metadata(json_path, db):
    meta = json.load(open(json_path, 'r', encoding='utf-8'))
    enriched = enrich_metadata(meta)
    episode_id = enriched.get('id') or os.path.basename(json_path)

    base_name = json_path.rsplit('.info.json', 1)[0]
    media_file = None
    for ext in ['.mp3', '.m4a', '.flac', '.wav', '.opus']:
        if os.path.exists(base_name + ext):
            media_file = base_name + ext
            break
    if not media_file:
        log(f"No media for {json_path}")
        return

    size = os.path.getsize(media_file)
    if episode_id in db and db[episode_id]['size'] == size:
        log(f"Skipping {episode_id}, already up-to-date.")
        return

    folder = os.path.join(MEDIA_ROOT, sanitize_filename(enriched["album"]))
    os.makedirs(folder, exist_ok=True)

    new_file_path = os.path.join(folder, enriched["episode_filename"])
    shutil.move(media_file, new_file_path)
    shutil.move(json_path, os.path.join(folder, os.path.basename(json_path)))

    write_tags(new_file_path, enriched["id3"])

    db[episode_id] = {
        "series": enriched["album"],
        "file": os.path.basename(new_file_path),
        "size": size,
        "last_seen": time.strftime('%Y-%m-%d')
    }
    log(f"Moved & tagged: {new_file_path}")

def download_series(url, db, ydl):
    log(f"Downloading from {url}")
    ydl.download([url])
    for root, dirs, files in os.walk(MEDIA_ROOT):
        for f in files:
            if f.endswith('.info.json'):
                process_metadata(os.path.join(root, f), db)

def main():
    db = load_db()
    ydl_opts = {
        'embedmetadata': True,
        'embedinfojson': True,
        'writeinfojson': True,
        'quiet': False,
        'outtmpl': os.path.join(MEDIA_ROOT, '%(title)s.%(ext)s'),
        'sleep_interval': 15,
        'max_sleep_interval': 157,
        'retries': 10
    }
    ydl = YoutubeDL(ydl_opts)

    websites = json.load(open(SERIES_FILE, 'r', encoding='utf-8'))
    for url in websites:
        download_series(url, db, ydl)

    save_db(db)

if __name__ == '__main__':
    main()
