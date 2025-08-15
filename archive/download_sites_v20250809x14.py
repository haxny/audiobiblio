import json
import requests
import os
import time
import random
import re
from yt_dlp import YoutubeDL

# Load URLs from JSON file
with open('websites_mujrozhlas.json', 'r', encoding='utf-8') as f:
    websites = json.load(f)

# Create folders
os.makedirs('html_files', exist_ok=True)
os.makedirs('media', exist_ok=True)

# yt-dlp options
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

def safe_filename(url):
    """Convert URL to safe filename."""
    filename = url.replace('https://', '').replace('http://', '')
    filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
    return filename + '.html'

def random_delay(min_s=15, max_s=157):
    delay = random.randint(min_s, max_s)
    print(f"Waiting {delay} seconds...")
    time.sleep(delay)

for url in websites:
    retries = 0
    max_retries = 5
    backoff = 60  # initial backoff for 429

    while retries <= max_retries:
        try:
            # Delay before any request
            random_delay()

            print(f"Downloading HTML from {url} ...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'
            }
            response = requests.get(url, headers=headers)

            if response.status_code == 429:
                retries += 1
                wait_time = backoff * retries
                print(f"HTTP 429 Too Many Requests. Sleeping {wait_time} seconds before retry {retries}/{max_retries}...")
                time.sleep(wait_time)
                continue  # try again

            response.raise_for_status()

            # Save HTML
            filepath = os.path.join('html_files', safe_filename(url))
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(response.text)
            print(f"Saved HTML to {filepath}")

            # Delay before media download
            random_delay()

            print(f"Downloading media from {url} ...")
            ydl.download([url])

            break  # success, go to next URL

        except requests.RequestException as e:
            retries += 1
            wait_time = backoff * retries
            print(f"Request failed ({e}). Waiting {wait_time}s before retry {retries}/{max_retries}...")
            time.sleep(wait_time)

        except Exception as e:
            print(f"Failed to process {url}: {e}")
            break
