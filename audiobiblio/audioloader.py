#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audioloader — MujRozhlas discovery + selective downloader

Highlights
----------
- Flat discovery (no media probing) to avoid 429s and slowness.
- URL-only mode: if you pass --url or positional URLs, the big list is skipped.
- Status table: Series / Status / Local vs Feed vs Expected
- Interactive picker (default): choose series/episodes to download.
- Optional unattended mode: process exactly the URLs provided.
- One-line download progress per file (yt-dlp progress hook).
- Optional post-process: call tag_fixer on the output folder.

Notes
-----
- Keep yt-dlp up to date inside your venv: `python3 -m pip install -U yt-dlp`
- DB lives at episodes_db.json next to this file.
- Output root: ./media/{_downloading,_progress,_complete,_truncated}
"""
from __future__ import annotations
import os
import re
import unicodedata
import sys
import shutil
import string
import logging
import argparse
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
    import requests
except ImportError as e:
    missing = e.name
    print(f"Missing dependency: {missing} (install: pip install {missing})")
    raise

from .tags.writer import write_tags
from .tags.genre import process_genre
from .tags.nfo import write_nfo_from_ytdlp

# --- yt_dlp helpers (flat vs rich) ------------------------------------------

YDL_OPTS_BASE = {
    "quiet": True,
    "skip_download": True,
    "noplaylist": True,
    "nocheckcertificate": True,
}

# Directories (media root)
DIR_ROOT = Path("media")
DIR_DOWNLOADING = DIR_ROOT / "_downloading"
DIR_COMPLETE = DIR_ROOT / "_complete"
DIR_TRUNCATED = DIR_ROOT / "_truncated"
AUDIO_EXTS = (".m4a", ".mp3", ".opus", ".flac", ".ogg", ".aac")

YDL_DL_OPTS = {
    "format": "bestaudio",
    "outtmpl": str(DIR_DOWNLOADING / "%(title)s [%(id)s].%(ext)s"),
    "download_archive": str(DIR_COMPLETE / "downloaded_archive.txt"),
    "embed_thumbnail": True,
    "postprocessors": [
        {"key": "FFmpegExtractAudio", "preferredcodec": "best"},
        {"key": "FFmpegMetadata", "add_metadata": True},
        {"key": "EmbedThumbnail"},
        # SponsorBlock is now configured via a top-level key
    ],
    "prefer_ffmpeg": True,
    "quiet": False,
    "noplaylist": False,
    "nocheckcertificate": True,
    # New configuration for SponsorBlock
    "sponsor_block_remove_actions": ["sponsor"],
}

def _clean_filename(s: str) -> str:
    """Sanitize string for use as a filename component."""
    valid_chars = f"-_.() {string.ascii_letters}{string.digits}"
    cleaned = ''.join(c for c in s if c in valid_chars)
    cleaned = unicodedata.normalize('NFKD', cleaned).encode('ascii', 'ignore').decode('utf-8')
    cleaned = cleaned.replace(" ", "_")
    return cleaned

def _get_title_from_info(info: dict) -> str:
    """Get a sane title from yt-dlp info dict."""
    title = info.get("title") or info.get("fulltitle") or info.get("id") or "Untitled"
    return _clean_filename(title)

def _copy_sidecars(src_file: Path, dest_dir: Path, info: dict) -> None:
    """Copy .json, .jpg, .description files to the new directory."""
    name_stem = src_file.stem
    for ext in (".info.json", ".jpg", ".description"):
        sidecar_src = src_file.parent / f"{name_stem}{ext}"
        if sidecar_src.exists():
            sidecar_dest = dest_dir / f"{_get_title_from_info(info)}{ext}"
            try:
                shutil.copy(sidecar_src, sidecar_dest)
            except Exception as e:
                print(f"  ! Failed to copy sidecar {sidecar_src}: {e}")

def _finalize_move(src_file: Path, info: dict) -> Path:
    """
    Moves audio file and its sidecars from `_downloading` to a structured `_complete` path.
    Returns the path to the new audio file.
    """
    if not src_file or not src_file.exists():
        print(f"  ! Source file does not exist: {src_file}")
        return Path("")

    # Create the destination folder based on info dict
    series_raw = info.get("series") or info.get("playlist_title") or "Unknown_Series"
    series_title = _clean_filename(series_raw) if isinstance(series_raw, str) else _get_title_from_info(series_raw)
    ep_title = _get_title_from_info(info)
    
    dest_dir = DIR_COMPLETE / series_title
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Rename and move the audio file
    dest_audio_path = dest_dir / f"{ep_title}{src_file.suffix}"
    try:
        shutil.move(str(src_file), str(dest_audio_path))
    except Exception as e:
        print(f"  ! Failed to move file: {src_file} to {dest_audio_path}. Error: {e}")
        return Path("")
    
    # Copy sidecars
    _copy_sidecars(src_file, dest_dir, info)

    print(f"  ✓ Moved to: {dest_audio_path}")
    return dest_audio_path

def _run_tag_fixer_on_file(final_audio: Path, info: dict) -> None:
    """Applies metadata tags to the audio file using the shared tags package."""
    if not final_audio.exists():
        print(f"  ! Cannot tag non-existent file: {final_audio}")
        return

    title = info.get("title") or ""
    artist = info.get("artist") or info.get("creator") or ""
    album = info.get("series") or info.get("playlist_title") or ""
    track_number = info.get("episode_number") or ""

    # Build source URL from info dict (webpage_url is the canonical page)
    www = info.get("webpage_url") or info.get("url") or ""

    album_tags = {
        "album": album,
        "artist": artist,
        "albumartist": artist,
        "genre": process_genre(info.get("genre", "")),
        "date": info.get("upload_date", "")[:4] if info.get("upload_date") else "",
        "publisher": info.get("channel") or "",
        "www": www,
    }
    track_tags = {
        "title": title,
        "tracknumber": str(track_number) if track_number else "",
    }

    try:
        write_tags(final_audio, album_tags, track_tags)
        print(f"  ✓ Tagged file: {final_audio.name}")
    except Exception as e:
        print(f"  ! Failed to tag file: {final_audio.name}. Error: {e}")


def download_one_episode(url: str, redownload: bool = False):
    """
    Download a single episode URL with yt-dlp using YDL_DL_OPTS.
    Returns (ok, filepath_or_None, info_dict_or_None).
    """
    ydl_opts = YDL_DL_OPTS.copy()

    if redownload:
        print("Forcing re-download by ignoring the download archive...")
        ydl_opts['download_archive'] = None

    # Track the final filename through yt-dlp hooks.
    # postprocessor_hooks fire after each PP (including FFmpegExtractAudio)
    # so the last filepath seen is the actual output file.
    final_filepath: list[Optional[Path]] = [None]

    def _pp_hook(d: dict):
        if d.get("status") == "finished":
            fp = d.get("info_dict", {}).get("filepath") or d.get("info_dict", {}).get("_filename")
            if fp and Path(fp).exists():
                final_filepath[0] = Path(fp)

    def _progress_hook(d: dict):
        if d.get("status") == "finished":
            fp = d.get("filename") or d.get("info_dict", {}).get("_filename")
            if fp and Path(fp).exists():
                final_filepath[0] = Path(fp)

    ydl_opts["progress_hooks"] = [_progress_hook]
    ydl_opts["postprocessor_hooks"] = [_pp_hook]

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        print(f"  ! Download failed: {e}")
        return False, None, None

    if isinstance(info, dict) and info.get("_type") == "playlist":
        print("  ! URL resolved to a playlist; per-episode downloader refuses playlist inputs.")
        return False, None, info

    filepath = final_filepath[0]

    # Fallback 1: check info_dict's filepath/requested_downloads
    if not filepath and isinstance(info, dict):
        for rd in info.get("requested_downloads", []):
            fp = rd.get("filepath")
            if fp and Path(fp).exists() and Path(fp).suffix.lower() in AUDIO_EXTS:
                filepath = Path(fp)
                break

    # Fallback 2: glob by episode ID (original method)
    if not filepath and isinstance(info, dict):
        ep_id = str(info.get("id") or "").strip()
        if ep_id:
            filepath = _find_downloaded_audio(DIR_DOWNLOADING, ep_id)

    if not filepath:
        print("  ! Could not locate downloaded audio file; leaving as-is.")

    return True, filepath, info

def _find_downloaded_audio(dir_path, ep_id: str) -> Optional[Path]:
    """Find a downloaded audio file by its episode ID."""
    for f in dir_path.glob(f"*{ep_id}.*"):
        if f.suffix.lower() in AUDIO_EXTS:
            return f
    return None

def download_batch(urls: list[str], args) -> None:
    """
    Download a batch of episode URLs sequentially using yt-dlp.
    After all downloads, writes a .nfo sidecar with full metadata.
    """
    if not urls:
        print("Nothing to download.")
        return

    print(f"\nStarting downloads ({len(urls)} episode(s))...")
    info_dicts: list[dict] = []
    dest_dir: Optional[Path] = None

    for i, url in enumerate(urls, start=1):
        print(f"\n[{i}/{len(urls)}] {url}")

        # PREFLIGHT (fast)
        ep_id, ep_title = "", ""
        try:
            flat = ydl_extract_flat(url)
            ep_id = str(flat.get("id") or "").strip()
            ep_title = str(flat.get("title") or "").strip()
        except Exception as e:
            logging.info(f"Preflight failed for {url}: {e}")

        # DOWNLOAD
        ok, src_file, info = download_one_episode(url, args.redownload)
        if not ok:
            continue

        if info:
            info_dicts.append(info)

        if src_file:
            # Move audio + sidecars to structured _complete path
            final_audio = _finalize_move(src_file, info or {})
            if final_audio and final_audio.parent.exists():
                dest_dir = final_audio.parent

            # TAG FIX (optional)
            if final_audio and getattr(args, "tag_fix", False):
                _run_tag_fixer_on_file(final_audio, info or {})

    # Write .nfo sidecar with all collected metadata
    if info_dicts and dest_dir:
        try:
            nfo_path = write_nfo_from_ytdlp(dest_dir, info_dicts)
            print(f"\n  ✓ Metadata saved: {nfo_path.name}")
        except Exception as e:
            print(f"\n  ! Failed to write .nfo: {e}")

    print("\nAll selected downloads processed.")

def ydl_extract_flat(url: str):
    """Extracts flat information from a URL using yt-dlp."""
    with YoutubeDL(YDL_OPTS_BASE) as ydl:
        return ydl.extract_info(url, download=False)

def _episode_state_in_complete(ep_id: str, ep_title: str) -> str:
    """Checks if an episode is already in the complete directory."""
    if not ep_id:
        return "UNKNOWN"
    # Check for a file containing the episode ID in its name
    if any(f for f in DIR_COMPLETE.glob(f"**/*{ep_id}*") if f.is_file()):
        return "COMPLETE"
    return "NEW"

def _expand_series_url(url: str) -> list[str]:
    """If url is a mujrozhlas series page, scrape all episode links. Otherwise return [url]."""
    parsed = urlparse(url)
    if "mujrozhlas.cz" not in parsed.netloc:
        return [url]
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}, timeout=30)
        r.raise_for_status()
    except Exception:
        return [url]
    # Find episode links that share the same slug base but have a numeric suffix
    path = parsed.path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    pattern = re.compile(rf'href="({re.escape(path)}-\d+)"')
    matches = sorted(set(pattern.findall(r.text)))
    if not matches:
        return [url]
    return [f"{parsed.scheme}://{parsed.netloc}{m}" for m in matches]


def main():
    parser = argparse.ArgumentParser(description="MujRozhlas audioloader")
    parser.add_argument("--url", nargs='+', help="URL(s) to download")
    parser.add_argument("--redownload", action='store_true', help="Force re-download of existing files")
    parser.add_argument("--tag-fix", action='store_true', help="Apply metadata tags to the audio files")
    args = parser.parse_args()
    
    # Ensure necessary directories exist before any downloads start
    DIR_ROOT.mkdir(parents=True, exist_ok=True)
    DIR_DOWNLOADING.mkdir(exist_ok=True)
    DIR_COMPLETE.mkdir(exist_ok=True)
    DIR_TRUNCATED.mkdir(exist_ok=True)

    if args.url:
        for url in args.url:
            print(f"Discovering: {url}")
            episode_urls = _expand_series_url(url)
            if len(episode_urls) > 1:
                print(f"  Found {len(episode_urls)} episodes in series")
            download_batch(episode_urls, args)
    else:
        print("Please provide a URL to download.")

if __name__ == '__main__':
    main()
