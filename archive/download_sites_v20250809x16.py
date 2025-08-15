import json
import requests
import os
import time
import random
import re
import logging
from yt_dlp import YoutubeDL

# ====== Logging setup ======
logging.basicConfig(
    filename='download.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)

def log(msg):
    print(msg)
    logging.info(msg)

# ====== Load URLs ======
with open('websites_mujrozhlas.json', 'r', encoding='utf-8') as f:
    websites = json.load(f)

# ====== Load already downloaded list ======
downloaded_file = 'downloaded.json'
if os.path.exists(downloaded_file):
    with open(downloaded_file, 'r', encoding='utf-8') as f:
        downloaded = set(json.load(f))
else:
    downloaded = set()

# ====== Create folders ======
os.makedirs('html_files', exist_ok=True)
os.makedirs('media', exist_ok=True)

# ====== yt-dlp options ======
ydl_opts = {
    'embedmetadata': True,
    'embedinfojson': True,
    'writeinfojson': True,
    'quiet': False,
    'outtmpl': 'media/%(title)s.%(ext)s',
    'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36',
    'sleep_interval': 15,
    'max_sleep_interval': 157,
    'retries': 10
}

ydl = YoutubeDL(ydl_opts)

# ====== Helper functions ======
def safe_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)

def random_delay(min_s=15, max_s=157):
    delay = random.randint(min_s, max_s)
    log(f"Waiting {delay} seconds...")
    time.sleep(delay)

def move_and_rename_by_metadata():
    """Move and rename downloaded files based on yt-dlp metadata JSON."""
    for fname in os.listdir('media'):
        if not fname.endswith('.info.json'):
            continue

        json_path = os.path.join('media', fname)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            album = metadata.get('album') or 'UnknownAlbum'
            release_time = metadata.get('release_timestamp') or metadata.get('upload_date')
            title = metadata.get('title') or 'Untitled'

            if release_time:
                if isinstance(release_time, int):
                    date_str = time.strftime('%Y%m%d', time.localtime(release_time))
                else:
                    date_str = str(release_time).replace('-', '')
            else:
                date_str = '00000000'

            base_name = fname.rsplit('.info.json', 1)[0]
            media_file = None
            for ext in ['.mp3', '.m4a', '.flac', '.wav', '.opus']:
                candidate = os.path.join('media', base_name + ext)
                if os.path.exists(candidate):
                    media_file = candidate
                    break

            if not media_file:
                log(f"No media file found for metadata {fname}")
                continue

            safe_album = safe_filename(album)
            folder_path = os.path.join('media', safe_album)
            os.makedirs(folder_path, exist_ok=True)

            new_filename = f"{date_str}_{safe_filename(title)}{os.path.splitext(media_file)[1]}"
            new_path = os.path.join(folder_path, new_filename)

            counter = 1
            while os.path.exists(new_path):
                new_filename = f"{date_str}_{safe_filename(title)}_{counter}{os.path.splitext(media_file)[1]}"
                new_path = os.path.join(folder_path, new_filename)
                counter += 1

            os.rename(media_file, new_path)
            log(f"Moved '{media_file}' → '{new_path}'")

            new_json_path = os.path.join(folder_path, base_name + '.info.json')
            os.rename(json_path, new_json_path)

        except Exception as e:
            log(f"Error processing metadata from {fname}: {e}")

# ====== Main loop ======
for url in websites:
    if url in downloaded:
        log(f"Skipping {url} (already downloaded)")
        continue

    retries = 0
    max_retries = 5
    backoff = 60

    while retries <= max_retries:
        try:
            random_delay()

            log(f"Downloading HTML from {url} ...")
            headers = {'User-Agent': ydl_opts['user_agent']}
            response = requests.get(url, headers=headers)

            if response.status_code == 429:
                retries += 1
                wait_time = backoff * retries
                log(f"HTTP 429 Too Many Requests. Sleeping {wait_time} seconds before retry {retries}/{max_retries}...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()

            html_filename = safe_filename(url.replace('https://', '').replace('http://', '')) + '.html'
            filepath = os.path.join('html_files', html_filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(response.text)
            log(f"Saved HTML to {filepath}")

            random_delay()

            log(f"Downloading media from {url} ...")
            ydl.download([url])

            move_and_rename_by_metadata()

            downloaded.add(url)
            with open(downloaded_file, 'w', encoding='utf-8') as f:
                json.dump(list(downloaded), f, ensure_ascii=False, indent=2)

            break

        except requests.RequestException as e:
            retries += 1
            wait_time = backoff * retries
            log(f"Request failed ({e}). Waiting {wait_time}s before retry {retries}/{max_retries}...")
            time.sleep(wait_time)

        except Exception as e:
            log(f"Failed to process {url}: {e}")
            break
