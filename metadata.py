#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ✅ Flexible imports — works in both direct-run and package-run modes
try:
    from .utils import (
        strip_diacritics,
        sanitize_filename,
        clean_tag_text,
        safe_int,
        safe_year,
        join_nonempty,
        extract_station_code
    )
except ImportError:
    from utils import (
        strip_diacritics,
        sanitize_filename,
        clean_tag_text,
        safe_int,
        safe_year,
        join_nonempty,
        extract_station_code
    )

def enrich_metadata(meta: dict) -> dict:
    """
    Fill in missing or clean up metadata fields for audio files.
    """
    # Example enrichment logic — adapt as needed to your original code
    meta['album'] = clean_tag_text(meta.get('album', 'Unknown Album'))
    meta['artist'] = clean_tag_text(meta.get('artist', 'Unknown Artist'))
    meta['title'] = clean_tag_text(meta.get('title', 'Untitled'))
    meta['year'] = safe_year(meta.get('date') or meta.get('year'))
    meta['episode_filename'] = sanitize_filename(
        f"{meta['artist']} - {meta['title']}.mp3"
    )
    meta['id3'] = {
        'title': meta['title'],
        'album': meta['album'],
        'artist': meta['artist'],
        'date': meta['year'],
    }
    return meta
