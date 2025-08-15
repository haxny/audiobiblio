#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadata_fetch.py
-----------------
Fetch structured book metadata from databazeknih.cz
and save to JSON for audiobook tagging integration.

Usage:
    python3 metadata_fetch.py "Robert Merle - Malevil"
    python3 metadata_fetch.py "/path/to/folder"
"""

import os
import re
import sys
import json
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.databazeknih.cz"

def clean_text(txt: str) -> str:
    """Normalize whitespace and strip."""
    return re.sub(r'\s+', ' ', txt).strip()

def guess_author_title(foldername: str):
    """
    Try to split a folder name like:
    'Robert Merle - Malevil' → author='Robert Merle', title='Malevil'
    """
    name = os.path.basename(foldername)
    parts = [p.strip() for p in name.split('-')]
    if len(parts) >= 2:
        author = parts[0]
        title = " - ".join(parts[1:])
    else:
        author = ""
        title = name
    return author, title

def fetch_databazeknih(title: str, author: str = ""):
    """
    Search databazeknih.cz and fetch book metadata.
    Returns dict or None if not found.
    """
    query = f"{title} {author}".strip()
    search_url = f"{BASE_URL}/search?q={requests.utils.quote(query)}"
    r = requests.get(search_url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    result_link = soup.select_one("div.search-book a")
    if not result_link:
        print("[!] No results found.")
        return None

    book_url = BASE_URL + result_link.get("href")
    print(f"[i] Found book page: {book_url}")

    r = requests.get(book_url, timeout=10)
    r.raise_for_status()
    page = BeautifulSoup(r.text, "html.parser")

    data = {
        "author": None,
        "translator": None,
        "performer": None,
        "publisher": None,
        "year": None,
        "series": None,
        "description": None,
        "cover_art": None,
        "sources": [book_url],
    }

    title_block = page.select_one("h1")
    if title_block:
        data["album"] = clean_text(title_block.get_text())

    author_block = page.select_one("h3 a")
    if author_block:
        data["author"] = clean_text(author_block.get_text())

    cover = page.select_one("img.book-cover")
    if cover and cover.get("src"):
        cover_url = cover["src"]
        if cover_url.startswith("/"):
            cover_url = BASE_URL + cover_url
        data["cover_art"] = cover_url

    details = page.select("div.book-detail div.row div.desc")
    for d in details:
        text = clean_text(d.get_text(" ", strip=True))
        if "Překlad" in text:
            m = re.search(r'Překlad:\s*(.*)', text)
            if m:
                data["translator"] = clean_text(m.group(1))
        if "Vydáno" in text:
            m = re.search(r'Vydáno:\s*([0-9]{4})', text)
            if m:
                data["year"] = m.group(1)
            m2 = re.search(r'Vydáno:\s*[0-9]{4}\s*,\s*(.*)', text)
            if m2:
                data["publisher"] = clean_text(m2.group(1))
        if "Originální název" in text:
            m = re.search(r'Originální název:\s*(.*?)([0-9]{4})', text)
            if m:
                data["original_title"] = clean_text(m.group(1))
                data["original_year"] = m.group(2)

    desc = page.select_one("div.perex")
    if desc:
        data["description"] = clean_text(desc.get_text())

    series_block = page.select_one("h4 a[href*='serie']")
    if series_block:
        series_name = clean_text(series_block.get_text())
        index_match = re.search(r'(\d+)\.', page.get_text())
        series_data = {"name": series_name}
        if index_match:
            series_data["index"] = int(index_match.group(1))
        data["series"] = series_data

    return data

def save_json(folder, data):
    path = os.path.join(folder, "_metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[+] Metadata saved to {path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 metadata_fetch.py <query or folder>")
        sys.exit(1)

    arg = sys.argv[1]
    if os.path.isdir(arg):
        author, title = guess_author_title(arg)
        print(f"[i] Guessing from folder: author='{author}', title='{title}'")
        meta = fetch_databazeknih(title, author)
        if meta:
            save_json(arg, meta)
    else:
        # treat as direct query string
        parts = [p.strip() for p in arg.split('-')]
        if len(parts) >= 2:
            author = parts[0]
            title = " - ".join(parts[1:])
        else:
            author = ""
            title = arg
        meta = fetch_databazeknih(title, author)
        if meta:
            print(json.dumps(meta, ensure_ascii=False, indent=2))
