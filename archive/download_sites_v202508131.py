import json, os, re, time, unicodedata, requests, logging
from yt_dlp import YoutubeDL
from urllib.parse import urlparse

logging.basicConfig(filename='download.log', level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', encoding='utf-8')
log = lambda m: (print(m), logging.info(m))

DB_FILE = 'episodes_db.json'
SERIES_FILE = 'websites_mujrozhlas.json'

def load_db():
    if os.path.exists(DB_FILE):
        return json.load(open(DB_FILE, 'r', encoding='utf-8'))
    return {}

def save_db(db):
    json.dump(db, open(DB_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

def normalize_text(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[<>:"/\\|?*]', '', text).strip()

def get_series_dir(metadata):
    return normalize_text(
        metadata.get('album') or
        metadata.get('playlist') or
        metadata.get('title') or
        metadata.get('webpage_url_basename') or
        urlparse(metadata.get('webpage_url', '')).path.rstrip('/').split('/')[-1] or
        'UnknownSeries'
    )

def get_filename(metadata, ext):
    series = get_series_dir(metadata)
    date = '00000000'
    release_time = metadata.get('release_timestamp') or metadata.get('upload_date')
    if release_time:
        if isinstance(release_time, int):
            date = time.strftime('%Y%m%d', time.localtime(release_time))
        else:
            date = str(release_time).replace('-', '')
    title = normalize_text(metadata.get('title') or 'Untitled')
    return f"{series} {date} {title}{ext}"

def process_metadata(json_path, db):
    meta = json.load(open(json_path, 'r', encoding='utf-8'))
    episode_id = meta['id']
    base_name = json_path.rsplit('.info.json', 1)[0]

    # Find media file
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

    # Create folder & move
    folder = os.path.join('media', get_series_dir(meta))
    os.makedirs(folder, exist_ok=True)
    new_file = os.path.join(folder, get_filename(meta, os.path.splitext(media_file)[1]))
    os.rename(media_file, new_file)
    os.rename(json_path, os.path.join(folder, os.path.basename(json_path)))

    # Update DB
    db[episode_id] = {
        "series": get_series_dir(meta),
        "file": os.path.basename(new_file),
        "size": size,
        "last_seen": time.strftime('%Y-%m-%d')
    }
    log(f"Moved to {new_file}")

def download_series(url, db, ydl):
    log(f"Downloading from {url}")
    ydl.download([url])
    for f in os.listdir('media'):
        if f.endswith('.info.json') and os.path.dirname(f) == 'media':
            process_metadata(os.path.join('media', f), db)

def main():
    db = load_db()
    ydl_opts = {
        'embedmetadata': True,
        'embedinfojson': True,
        'writeinfojson': True,
        'quiet': False,
        'outtmpl': 'media/%(title)s.%(ext)s',
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
