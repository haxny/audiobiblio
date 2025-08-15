#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadata_fetch.py
-----------------
Fetch structured book metadata from databazeknih.cz
(for audiobook tagging integration).

Author: Your name
Date: 2025-08-14
"""

import requests
from bs4 import BeautifulSoup
import re

BASE_URL = "https://www.databazeknih.cz"

def clean_text(txt: str) -> str:
    """Normalize whitespace and strip."""
    return re.sub(r'\s+', ' ', txt).strip()

def fetch_databazeknih(title: str, author: str = ""):
    """
    Search databazeknih.cz and fetch book metadata.
    Returns dict or None if not found.
    """
    # --- 1) Perform search ---
    query = f"{title} {author}".strip()
    search_url = f"{BASE_URL}/search?q={requests.utils.quote(query)}"
    r = requests.get(search_url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    
    # --- 2) Find first search result ---
    result_link = soup.select_one("div.search-book a")
    if not result_link:
        print("[!] No results found.")
        return None

    book_url = BASE_URL + result_link.get("href")
    print(f"[i] Found book page: {book_url}")

    # --- 3) Fetch book details page ---
    r = requests.get(book_url, timeout=10)
    r.raise_for_status()
    page = BeautifulSoup(r.text, "html.parser")

    data = {
        "author": None,
        "translator": None,
        "performer": None,  # not usually listed on databazeknih
        "publisher": None,
        "year": None,
        "series": None,
        "description": None,
        "cover_art": None,
        "sources": [book_url],
    }

    # --- 4) Title and Author ---
    # Top title block usually contains both
    title_block = page.select_one("h1")
    if title_block:
        data["album"] = clean_text(title_block.get_text())

    author_block = page.select_one("h3 a")
    if author_block:
        data["author"] = clean_text(author_block.get_text())

    # --- 5) Cover Art ---
    cover = page.select_one("img.book-cover")
    if cover and cover.get("src"):
        # If relative link, prepend BASE_URL
        cover_url = cover["src"]
        if cover_url.startswith("/"):
            cover_url = BASE_URL + cover_url
        data["cover_art"] = cover_url

    # --- 6) Extract detail items ---
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
            # also possible to extract publisher
            m2 = re.search(r'Vydáno:\s*[0-9]{4}\s*,\s*(.*)', text)
            if m2:
                data["publisher"] = clean_text(m2.group(1))
        if "Originální název" in text:
            m = re.search(r'Originální název:\s*(.*?)([0-9]{4})', text)
            if m:
                # if original year differs, prefer it as 'original_year'
                data["original_title"] = clean_text(m.group(1))
                data["original_year"] = m.group(2)

    # --- 7) Description ---
    desc = page.select_one("div.perex")
    if desc:
        data["description"] = clean_text(desc.get_text())

    # --- 8) Series detection ---
    series_block = page.select_one("h4 a[href*='serie']")
    if series_block:
        # If we have a series, the index is often near the title
        series_name = clean_text(series_block.get_text())
        # Try to detect index from page
        index_match = re.search(r'(\d+)\.', page.get_text())
        series_data = {"name": series_name}
        if index_match:
            series_data["index"] = int(index_match.group(1))
        data["series"] = series_data

    return data


if __name__ == "__main__":
    # Quick test with Robert Merle - Malevil
    meta = fetch_databazeknih("Malevil", "Robert Merle")
    if meta:
        for k, v in meta.items():
            print(f"{k:12}: {v}")
