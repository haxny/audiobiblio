import json
import requests
import os
import time
import random
from yt_dlp import YoutubeDL

# Load URLs from JSON file
with open('websites_mujrozhlas.json', 'r') as f:
    websites = json.load(f)

# Folder to save HTML files
os.makedirs('html_files', exist_ok=True)
os.makedirs('media', exist_ok=True)

# Setup yt-dlp options
ydl_opts = {
    'embedmetadata': True,      # embed metadata into media files
    'embedinfojson': True,      # embed info JSON inside media files
    'writeinfojson': True,      # save info JSON files separately
    'quiet': False,             # show yt-dlp output so you see progress
    'outtmpl': 'media/%(title)s.%(ext)s',  # media output folder
    'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36',
    'sleep_interval': 15,
    'max_sleep_interval': 157,
    'retries': 33,  # retry a few times if rate-limited
}

# Create a YoutubeDL instance once
ydl = YoutubeDL(ydl_opts)

for url in websites:
    try:
        delay = random.randint(15, 157)
        print(f"Waiting {delay} seconds before downloading {url}...")
        time.sleep(delay)  # wait BEFORE making any requests

        print(f"Downloading {url} HTML...")
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36'}
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            print("Hit rate limit! Sleeping for 5 minutes...")
            time.sleep(300)
            continue  # skip or retry later
        response.raise_for_status()

        filename = url.replace('https://', '').replace('http://', '').replace('/', '_') + '.html'
        filepath = os.path.join('html_files', filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(response.text)
        print(f"Saved {filepath}")

        print(f"Downloading media from {url} ...")
        ydl.download([url])

    except Exception as e:
        print(f"Failed to download {url}: {e}")
