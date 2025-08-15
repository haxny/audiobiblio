import json
import requests
import os
import time
import random
import re
import logging
from yt_dlp import YoutubeDL
from mutagen import File as MutagenFile

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

def check_and_rename_media_files():
    """Check downloaded media for 'album' tag and rename file if needed."""
    for fname in os.listdir('media'):
        path = os.path.join('media', fname)
        if not os.path.isfile(path):
            continue
        try:
            audio = MutagenFile(path, easy=True)
            if audio is None:
                continue
            album = audio.get('album', [None])[0]
            if album:
                ext = os.path.splitext(fname)[1]
                new_name = safe_filename(album) + ext
                new_path = os.path.join('media', new_name)

                # Handle duplicates
                counter = 1
                while os.path.exists(new_path):
                    new_name = f"{safe_filename(album)}_{counter}{ext}"
                    new_path = os.path.join('media', new_name)
                    counter += 1

                os.rename(path, new_path)
                log(f"Renamed '{fname}' → '{new_name}' (from album tag)")
        except Exception as e:
            log(f"Could not read tags from {fname}: {e}")

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
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'}
            response = requests.get(url, headers=headers)

            if response.status_code == 429:
                retries += 1
                wait_time = backoff * retries
                log(f"HTTP 429 Too Many Requests. Sleeping {wait_time} seconds before retry {retries}/{max_retries}...")
                time.sleep(wait_time)
                continue

            response.raise_for_status()

            # Save HTML
            html_filename = safe_filename(url.replace('https://', '').replace('http://', '')) + '.html'
            filepath = os.path.join('html_files', html_filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(response.text)
            log(f"Saved HTML to {filepath}")

            # Delay before media
            random_delay()

            log(f"Downloading media from {url} ...")
            ydl.download([url])

            # Check ID3 tags and rename
            check_and_rename_media_files()

            # Add to downloaded list
            downloaded.add(url)
            with open(downloaded_file, 'w', encoding='utf-8') as f:
                json.dump(list(downloaded), f, ensure_ascii=False, indent=2)

            break  # success, go to next URL

        except requests.RequestException as e:
            retries += 1
            wait_time = backoff * retries
            log(f"Request failed ({e}). Waiting {wait_time}s before retry {retries}/{max_retries}...")
            time.sleep(wait_time)

        except Exception as e:
            log(f"Failed to process {url}: {e}")
            break
