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
import json
import os
import re
import unicodedata
import sys
import glob
import shutil
import string
import subprocess
import time
import logging
import argparse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urlparse

try:
    from yt_dlp import YoutubeDL
except ImportError:
    print("Missing dependency: yt-dlp (install in your venv: pip install yt-dlp)")
    raise

# --- yt_dlp helpers (flat vs rich) ------------------------------------------

YDL_OPTS_BASE = {
    "quiet": True,
    "skip_download": True,
    "noplaylist": True,
    "nocheckcertificate": True,
}

def ydl_extract_flat(url: str) -> dict:
    """
    Fast metadata probe for a page/episode without fetching formats or media.
    Returns a small info dict with keys like 'id', 'title', etc.
    """
    opts = dict(YDL_OPTS_BASE)
    opts["extract_flat"] = True
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def ydl_extract_rich(url: str) -> dict:
    """Full metadata with formats (for per-episode download)."""
    opts = dict(YDL_OPTS_BASE)
    opts["extract_flat"] = False
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def _sanitize(s: str) -> str:
    s = s or ""
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def episode_looks_downloaded(ep: dict) -> bool:
    """
    Heuristic: we consider an episode 'downloaded' if a file name containing the
    episode's id or sanitized title exists in _complete, _progress, or _downloading.
    """
    ep_id = (ep.get("id") or "").strip()
    ep_title = _sanitize(ep.get("title") or "")

    roots = [DIR_COMPLETE, DIR_PROGRESS, DIR_DOWNLOADING]
    haystacks = []
    for root in roots:
        if os.path.isdir(root):
            for _, _, files in os.walk(root):
                haystacks.extend(files)

    if not haystacks:
        return False

    if ep_id:
        for name in haystacks:
            if ep_id in name:
                return True

    if ep_title:
        for name in haystacks:
            if _sanitize(name).find(ep_title) != -1:
                return True

    return False

def _sidecars_for_basename(base_path: Path) -> list[Path]:
    # base_path is WITHOUT extension already (e.g., ".../Title [12345]")
    exts = [".info.json", ".description", ".jpg", ".jpeg", ".png", ".webp"]
    out = []
    for ext in exts:
        p = base_path.with_suffix(base_path.suffix + ext)
        if p.exists():
            out.append(p)
    return out

def _any_audio_with_id(id_str: str, roots: list[str]) -> list[Path]:
    hits = []
    needle = f"[{id_str}]"
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, _, files in os.walk(root):
            for f in files:
                if needle in f and not f.endswith(".part"):
                    hits.append(Path(dp) / f)
    return hits

def episode_state(ep: dict) -> str:
    """
    Return 'NEW' | 'INCOMPLETE' | 'HAVE' based on what we find in _complete/_progress.
    - NEW: no audio found
    - INCOMPLETE: audio exists but at least one sidecar missing
    - HAVE: audio and basic sidecars (info.json) present
    """
    ep_id = (ep.get("id") or "").strip()
    if not ep_id:
        return "NEW"

    audios = _any_audio_with_id(ep_id, [DIR_COMPLETE, DIR_PROGRESS])
    if not audios:
        return "NEW"

    # Check sidecars for each audio; if any audio has missing info.json, call it INCOMPLETE
    # "basic" sidecar = .info.json; others are nice-to-have but we can be strict if you prefer
    for a in audios:
        base = a.with_suffix("")  # drop .m4a
        info_json = base.with_suffix(base.suffix + ".info.json")
        if not info_json.exists():
            return "INCOMPLETE"

    return "HAVE"

# --- unsorted small helpers ------------------------------------------------------

AUDIO_EXTS = (".m4a", ".mp3", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac")

SAFE_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

def _path_segments(u: str) -> list[str]:
    try:
        p = urlparse(u)
        return [s for s in p.path.strip("/").split("/") if s]
    except Exception:
        return []

def program_root_from_url(u: str) -> str | None:
    segs = _path_segments(u)
    return f"https://{urlparse(u).netloc}/{segs[0]}" if segs else None

def strip_diacritics(s: str) -> str:
    if not s:
        return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nf if not unicodedata.combining(ch))

def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def series_title_key(title: str) -> str:
    """
    Build a grouping key from an episode title.
    Heuristics:
      - take part up to first ' – ' or ' - ' or '.' if it looks like a series header,
      - otherwise use left side of colon if pattern 'Author: Book' exists,
      - else use the first two clauses split by ':' or ' - '.
    Then lowercase, strip diacritics and punctuation, collapse spaces.
    """
    t = _clean_spaces(title)
    if not t:
        return ""

    # Prefer "Author: Book" style
    if ":" in t:
        left, right = t.split(":", 1)
        # Many MujRozhlas titles are like "Ian McEwan: Betonová zahrada"
        candidate = f"{left.strip()}: {right.strip().split('.')[0]}"
    else:
        # fallback: stop at first sentence/long dash
        candidate = re.split(r"( – | - |\.)", t, maxsplit=1)[0]

    key = strip_diacritics(candidate).lower()
    key = re.sub(r"[^a-z0-9 ]+", " ", key)
    key = _clean_spaces(key)
    return key

def group_program_entries_by_series_key(entries: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        title = (e.get("title") or "").strip()
        key = series_title_key(title) or "_unknown_"
        groups.setdefault(key, []).append(e)
    return groups

def best_matching_group(groups: dict[str, list[dict]], episode_title: str) -> tuple[str, list[dict]] | None:
    """
    Pick the group whose key overlaps most with the current episode title key.
    Simple token overlap score.
    """
    target = series_title_key(episode_title)
    if not target or not groups:
        return None
    tgt_tokens = set(target.split())
    best = None
    best_score = -1
    for key, lst in groups.items():
        toks = set(key.split())
        score = len(tgt_tokens & toks)
        if score > best_score:
            best_score, best = score, (key, lst)
    return best

def _find_audio_by_id(workdir: str, ep_id: str) -> str | None:
    """Find newest finished audio file containing [<id>] in its name."""
    if not ep_id:
        return None
    hits = []
    needle = f"[{ep_id}]"
    for p in Path(workdir).glob("*"):
        if p.is_file() and (p.suffix.lower() in AUDIO_EXTS) and needle in p.name and not p.name.endswith(".part"):
            hits.append(p)
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(hits[0])

def _episode_state_in_complete(ep_id: str) -> str:
    """
    Return 'DONE' if audio + .info.json exist in _complete,
           'INCOMPLETE' if audio exists but sidecar missing,
           'MISSING' otherwise.
    """
    audio = _find_audio_by_id(DIR_COMPLETE, ep_id)
    if not audio:
        return "MISSING"
    base = Path(audio).with_suffix("")  # drop extension; keeps [id]
    info = base.with_suffix(base.suffix + ".info.json")
    return "DONE" if info.exists() else "INCOMPLETE"

def _url_parts(u: str) -> list[str]:
    """Split path into parts without leading/trailing slashes."""
    try:
        return urlparse(u).path.strip("/").split("/")
    except Exception:
        return []

def program_root_from_episode(u: str) -> str:
    """For /program-slug/book-slug/... return '/program-slug' as absolute URL base."""
    parts = _url_parts(u)
    if len(parts) >= 1:
        return f"https://{urlparse(u).netloc}/{parts[0]}"
    return u  # fallback

def book_slug_from_url(u: str) -> str:
    """Grab the 'book' (series) slug (2nd segment) if present."""
    parts = _url_parts(u)
    return parts[1] if len(parts) >= 2 else ""

def slugify_title(slug: str) -> str:
    """Humanize a slug for display."""
    s = slug.replace("-", " ").strip()
    if not s:
        return "Unknown"
    # keep the program’s language casing; just capitalize first letter
    return s[0].upper() + s[1:]

def parse_episode_number(title: str) -> tuple[int | None, int | None]:
    """
    Try to parse numbering like:
      '1. díl', '1 díl', '1/10', '1 / 10', '1. část', etc.
    Returns (episode_number, total_if_any)
    """
    t = title or ""

    # 1) 1/10 style
    m = re.search(r'(\d+)\s*/\s*(\d+)', t)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 2) '1. díl' or '1 díl' or '1. část'
    m = re.search(r'(\d+)\s*[\.\)]?\s*(díl|dil|část|cast|epizoda)', t, re.IGNORECASE)
    if m:
        return int(m.group(1)), None

    # 3) yt-dlp sometimes gives playlist_index
    # (handled elsewhere if present)

    return None, None

def episode_sort_key(entry: dict) -> tuple:
    """
    Sort by detected number, then title.
    Falls back gracefully.
    """
    n = None
    if entry.get("playlist_index") is not None:
        n = entry["playlist_index"]
    else:
        n, _total = parse_episode_number(entry.get("title") or "")
    # None should sort after numbers; use large sentinel
    numeric = n if isinstance(n, int) else 10**9
    return (numeric, (entry.get("title") or ""))

def group_program_entries_by_book_slug(entries: list[dict]) -> dict[str, list[dict]]:
    """
    Group program entries by the 'book' slug (the 2nd segment in URL).
    """
    groups: dict[str, list[dict]] = {}
    for e in entries:
        u = e.get("url") or ""
        slug = book_slug_from_url(u)
        groups.setdefault(slug, []).append(e)

    # sort each group by parsed episode number (then title)
    for slug, items in groups.items():
        items.sort(key=episode_sort_key)

    return groups

def _safe(s: str) -> str:
    s = (s or "").strip()
    s = strip_diacritics(s)
    s = "".join(ch if ch in SAFE_CHARS else " " for ch in s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "Unknown"

def _dest_folder_from_info(info: dict) -> str:
    """
    Build folder: _complete/<Program>/<SeriesKey>/
    Program = first path segment of the page URL (if present) else 'Program'
    SeriesKey = our series_title_key(title) prettified.
    """
    page_url = (info.get("webpage_url") or info.get("original_url") or info.get("url") or "").strip()
    prog = "Program"
    segs = _path_segments(page_url)
    if segs:
        prog = _safe(segs[0])

    title = (info.get("title") or "").strip()
    key = series_title_key(title)
    series_pretty = (key.title() if key and key != "_unknown_" else "Unknown Series")
    series_pretty = _safe(series_pretty)

    dest = os.path.join(DIR_COMPLETE, prog, series_pretty)
    os.makedirs(dest, exist_ok=True)
    return dest

def _sidecars_for_audio(path: str) -> list[str]:
    """
    Given /path/file.ext, return the sidecars we expect (if present):
      file.info.json, file.description, file.jpg/png/webp
    """
    p = Path(path)
    base = p.with_suffix("")  # keep "[id]" part, drop extension
    cand = [
        base.with_suffix(base.suffix + ".info.json"),
        base.with_suffix(base.suffix + ".description"),
    ]
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        cand.append(base.with_suffix(base.suffix + ext))
    return [str(c) for c in cand if c.exists()]

def _finalize_move(src_audio: str, info: dict) -> str:
    """
    Move the audio and any sidecars from _downloading to the final _complete/<Program>/<Series> folder.
    Return the final audio path.
    """
    dest_dir = _dest_folder_from_info(info)
    basename = os.path.basename(src_audio)
    dest_audio = os.path.join(dest_dir, basename)

    # move audio
    try:
        shutil.move(src_audio, dest_audio)
        print(f"  ✓ Moved audio → {dest_audio}")
    except Exception as e:
        print(f"  ! Move failed, keeping in _downloading: {e}")
        dest_audio = src_audio  # leave where it is

    # move sidecars
    for sc in _sidecars_for_audio(src_audio):
        try:
            shutil.move(sc, os.path.join(dest_dir, os.path.basename(sc)))
            print(f"  ✓ Moved sidecar: {os.path.basename(sc)}")
        except Exception as e:
            print(f"  ! Sidecar move failed: {os.path.basename(sc)} → {e}")

    return dest_audio

# --- Paths -----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'episodes_db.json')
SERIES_FILE = os.path.join(BASE_DIR, 'websites_mujrozhlas.json')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
DIR_DOWNLOADING = os.path.join(MEDIA_ROOT, '_downloading')
DIR_PROGRESS = os.path.join(MEDIA_ROOT, '_progress')
DIR_COMPLETE = os.path.join(MEDIA_ROOT, '_complete')
DIR_TRUNCATED = os.path.join(MEDIA_ROOT, '_truncated')

for d in (MEDIA_ROOT, DIR_DOWNLOADING, DIR_PROGRESS, DIR_COMPLETE, DIR_TRUNCATED):
    os.makedirs(d, exist_ok=True)

# --- Logging (console + file, robust) --------------------------------------

import logging, sys, os

LOG_FILE = os.path.join(BASE_DIR, "audioloader.log")

logger = logging.getLogger("audiobiblio.audioloader")
if not logger.handlers:  # prevent duplicate handlers on reload
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

def log(msg: str): logger.info(msg)
def warn(msg: str): logger.warning(msg)
def err(msg: str): logger.error(msg)
def exc(msg: str): logger.exception(msg)  # use inside except blocks

# --- Data models -----------------------------------------------------------
@dataclass
class Episode:
    id: str
    title: str
    playlist_index: Optional[int] = None
    url: Optional[str] = None
    duration: Optional[int] = None
    upload_date: Optional[str] = None  # YYYYMMDD if available

@dataclass
class SeriesSnapshot:
    series_uuid: str
    series_title: str
    series_url: str
    channel_url: str
    channel_title: Optional[str] = None
    total_in_feed: int = 0
    expected_total: Optional[int] = None
    episodes: Dict[str, Episode] = field(default_factory=dict)

# --- DB helpers ------------------------------------------------------------
def load_db() -> Dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_db(db: Dict) -> None:
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def db_mark_episode_downloaded(db: Dict, info: dict):
    """
    Mark an episode as downloaded in the simple DB so the picker can classify it as known next time.
    Since we may not have a good series UUID here, we store by a flat 'episodes' dict keyed by ID.
    """
    eid = (info or {}).get("id")
    title = (info or {}).get("title") or ""
    if not eid:
        return
    root = db.setdefault("_flat_episodes", {})
    if eid not in root:
        root[eid] = {"title": title, "ts": int(time.time())}
    save_db(db)

# --- yt-dlp helpers --------------------------------------------------------
# discovery: flat + lazy, no formats probing
YDL_DISCOVER = {
    'quiet': True,
    'skip_download': True,
    'extract_flat': 'in_playlist',
    'lazy_playlist': True,
    'retries': 10,
    'fragment_retries': 10,
    'retry_sleep': 2,
    'socket_timeout': 30,
    'http_headers': {'User-Agent': 'Mozilla/5.0 audiobiblio/yt-dlp'},
}
# rich: for actual download or when we really need formats
YDL_RICH = {
    'quiet': False,
    'skip_download': True,
    'extract_flat': False,
    'retries': 10,
    'fragment_retries': 10,
    'retry_sleep': 2,
    'socket_timeout': 30,
    'http_headers': {'User-Agent': 'Mozilla/5.0 audiobiblio/yt-dlp'},
}

def ydl_extract_light(url: str) -> dict:
    with YoutubeDL(YDL_DISCOVER) as ydl:
        return ydl.extract_info(url, download=False)

def ydl_extract_rich(url: str) -> dict:
    with YoutubeDL(YDL_RICH) as ydl:
        return ydl.extract_info(url, download=False)

# Options used for REAL downloads (per-episode)
YDL_DL_OPTS = {
    "quiet": False,
    "skip_download": False,
    "noplaylist": True,                      # single-episode only = do NOT pull whole show playlists
    "paths": {"home": DIR_DOWNLOADING},      # all (temp) outputs into _downloading
    "outtmpl": "%(title)s [%(id)s].%(ext)s", # stable filenames with Rozhlas ID in the filename
    "restrictfilenames": False,
    "continuedl": True,
    "ignoreerrors": "only_download",

    # Sidecars (so tagging has metadata to use):
    "writeinfojson": True,
    "writedescription": True,
    "writethumbnail": True,
    
    # Be nice to servers / more robust:
    "retries": 3,
    "concurrent_fragment_downloads": 1,

    # (Optional) force a UA if you see more 403/429:
    # "http_headers": {"User-Agent": "Mozilla/5.0"},
    "http_headers": {"User-Agent": "Mozilla/5.0 audiobiblio/yt-dlp"},
 
    "download_archive": os.path.join(MEDIA_ROOT, "downloaded.txt"),
    "overwrites": False,
}

def safe_extract_light(url: str, retries: int = 5, nap: float = 2.0) -> dict:
    last = None
    for i in range(retries):
        try:
            info = ydl_extract_light(url)
            if info and info.get('webpage_url','').rstrip('/') == 'https://www.mujrozhlas.cz':
                raise RuntimeError('Got homepage instead of target page')
            return info
        except Exception as e:
            last = e
            # simple exponential with jitter
            delay = nap * (2 ** i) + (0.5 * i)
            time.sleep(delay)
    raise last or RuntimeError('extract failed')


# --- Discovery logic -------------------------------------------------------
def derive_series_url_from_episode(ep_url: str) -> str:
    try:
        parts = ep_url.rstrip('/').split('/')
        if len(parts) > 3:
            return '/'.join(parts[:-1])
        return ep_url
    except Exception:
        return ep_url

def gather_series_from_channel(channel_url: str) -> Dict[str, SeriesSnapshot]:
    """Return mapping: series_key -> SeriesSnapshot for a given URL (show/series, or episode)."""
    data = safe_extract_light(channel_url)
    channel_title = data.get('title') or data.get('playlist_title')

    # Normalize entries list in flat mode
    if data.get('_type') == 'playlist':
        entries = data.get('entries') or []
        series_title = data.get('title') or 'Unknown Series'
        series_uuid = str(data.get('id') or '')
        series_url = data.get('webpage_url') or channel_url
        expected_total = data.get('playlist_count')
        ss = SeriesSnapshot(
            series_uuid=series_uuid or series_url,
            series_title=series_title,
            series_url=series_url,
            channel_url=channel_url,
            channel_title=channel_title,
            total_in_feed=0,
            expected_total=expected_total,
        )
        for entry in entries:
            # entry is flat; has only id/url/title/ie_key/playlist_index, etc.
            ep_id = str(entry.get('id') or '')
            ep_title = entry.get('title') or f"Episode {entry.get('playlist_index') or '?'}"
            ep_url = entry.get('url') or entry.get('webpage_url') or channel_url
            ep_idx = entry.get('playlist_index')
            ss.episodes[ep_id] = Episode(
                id=ep_id, title=ep_title, url=ep_url, playlist_index=ep_idx)
        ss.total_in_feed = len(ss.episodes)
        return {ss.series_uuid: ss}

    # Single episode flat URL (or direct page)
    ep_id = str(data.get('id') or '')
    ep_title = data.get('title') or 'Episode'
    ep_url = data.get('webpage_url') or channel_url
    series_url = derive_series_url_from_episode(ep_url)
    ss = SeriesSnapshot(
        series_uuid=series_url,
        series_title=data.get('playlist_title') or data.get('series') or 'Single',
        series_url=series_url,
        channel_url=channel_url,
        channel_title=channel_title,
    )
    ss.episodes[ep_id] = Episode(id=ep_id, title=ep_title, url=ep_url,
                                 playlist_index=data.get('playlist_index'))
    ss.total_in_feed = 1
    return {ss.series_uuid: ss}

def resolve_to_mujrozhlas(u: str) -> str:
    try:
        info = safe_extract_light(u)  # flat & fast
        w = (info.get("webpage_url") or info.get("original_url") or u).strip()
        # if extractor handed us a direct episode on mujrozhlas, prefer that
        if "mujrozhlas.cz" in w:
            return w
        # some entries expose a nested url
        cand = (info.get("url") or "").strip()
        if "mujrozhlas.cz" in cand:
            return cand
    except Exception:
        pass
    return u

def discover_program_episodes(program_url: str) -> list[dict]:
    """
    Extract a flat list of episodes for a program (series root) page.
    Returns dicts with: id, title, url, playlist_index, duration.
    Robust to non-playlist pages and flaky children.
    """
    # Use the safe/light extractor (flat, retries, backoff)
    try:
        data = safe_extract_light(program_url)
    except Exception as e:
        logging.info(f"discover_program_episodes: extract failed for {program_url}: {e}")
        return []

    entries = data.get("entries")

    # If we didn't get a playlist, treat it as a single entry page.
    if not entries:
        if data.get("_type") != "playlist":
            url = (data.get("webpage_url") or data.get("url") or "").strip()
            if not url:
                return []
            return [{
                "id": str(data.get("id") or ""),
                "title": data.get("title") or "(bez názvu)",
                "url": url,
                "playlist_index": data.get("playlist_index"),
                "duration": data.get("duration"),
            }]
        return []

    eps: list[dict] = []
    for e in entries:
        try:
            if not isinstance(e, dict):
                continue
            url = (e.get("webpage_url") or e.get("url") or "").strip()
            if not url:
                continue
            eps.append({
                "id": str(e.get("id") or ""),
                "title": e.get("title") or "(bez názvu)",
                "url": url,
                "playlist_index": e.get("playlist_index"),
                "duration": e.get("duration"),
            })
        except Exception as child_err:
            logging.debug(f"discover_program_episodes: skipping child: {child_err}")
            continue

    return eps

# --- Episode picker ---------------------------------------------------------
#ex# def prompt_select_episodes(episodes: list[dict], preselect: list[int] | None, args=None) -> list[dict]:

def _episode_state(ep: dict) -> str:
    """
    Classify an episode for the picker.

    Returns one of: "NEW", "INCOMPLETE", "DONE"
    - NEW:       no audio in _complete
    - INCOMPLETE: audio exists in _complete but one of the sidecars is missing
    - DONE:      audio + sidecars exist in _complete
    """
    ep_id = (ep.get("id") or "").strip()
    title = (ep.get("title") or "").strip()

    if not ep_id and not title:
        return "NEW"

    # look for audio in _complete
    audio = None
    for p in Path(DIR_COMPLETE).glob("*"):
        if not p.is_file():
            continue
        name = p.name
        if ep_id and f"[{ep_id}]" in name:
            audio = p
            break
        if title and title in name:
            audio = p
            break

    if not audio:
        return "NEW"

    # sidecars next to the audio
    base = audio.with_suffix("")  # strip .ext
    sidecars = [
        base.with_suffix(base.suffix + ".info.json"),
        base.with_suffix(base.suffix + ".description"),
    ]
    thumb_ok = any(base.with_suffix(base.suffix + ext).exists()
                   for ext in (".jpg", ".jpeg", ".png", ".webp"))
    have_all = all(s.exists() for s in sidecars) and thumb_ok
    return "DONE" if have_all else "INCOMPLETE"

def prompt_select_episodes(
    episodes: list[dict],
    preselect: list[int] | None = None,
    *,
    semi_robot: bool = True,
    redownload_missing: bool = False,
    redownload: bool = False,
) -> list[dict]:
    """
    Show a neat table and return the chosen episode dicts.
    If semi_robot=True and user presses ENTER, we auto-select:
      - all NEW, plus INCOMPLETE when redownload_missing=True
    """
    if not episodes:
        print("No episodes found.")
        return []

    # Build rows + default suggestion set
    suggested_indices: list[int] = []
    rows = []
    for i, ep in enumerate(episodes, start=1):
        st = _episode_state(ep)  # NEW / INCOMPLETE / DONE
        title = (ep.get("title") or "")[:68]
        rows.append((i, st, title))
        if st == "NEW":
            suggested_indices.append(i)
        elif st == "INCOMPLETE" and redownload_missing:
            suggested_indices.append(i)

    # Header
    print("\nAvailable Episodes:")
    print("Idx  Status       Title")
    print("---- ------------ ------------------------------------------------------------")
    for i, st, title in rows:
        pad = " " if i < 10 else ""
        print(f"{i:02d}.{pad} {st:<12} {title}")

    # Help
    print("\nOptions:")
    print("  ENTER   = (semi-robot) download suggested set" if semi_robot else "  ENTER   = cancel")
    print("  all     = download all NOT-DONE")
    print("  new     = only NEW")
    print("  inc     = only INCOMPLETE")
    print("  redo    = re-download ALL (ignores DONE)  [requires --redownload]")
    print("  1 3-5   = pick individual indices / ranges")
    print()

    # Preselect print (optional)
    if preselect:
        print(f"Preselected: {' '.join(str(x) for x in preselect)}")

    ans = input("Select episodes to download: ").strip()

    def expand_tokens(tokens: list[str]) -> list[int]:
        out: list[int] = []
        for tok in tokens:
            if "-" in tok:
                a, b = tok.split("-", 1)
                try:
                    a, b = int(a), int(b)
                    out.extend(list(range(min(a, b), max(a, b) + 1)))
                except ValueError:
                    pass
            else:
                try:
                    out.append(int(tok))
                except ValueError:
                    pass
        # clamp & dedupe
        out = [x for x in out if 1 <= x <= len(episodes)]
        return sorted(set(out))

    # ENTER behavior
    if ans == "":
        if semi_robot:
            # suggested set
            idxs = suggested_indices
            # fallback: if nothing suggested and user enabled --redownload, take all
            if not idxs and redownload:
                idxs = list(range(1, len(episodes) + 1))
            if not idxs:
                print("Nothing suggested. Cancel.")
                return []
            return [episodes[i - 1] for i in idxs]
        else:
            print("Cancelled.")
            return []

    # Keywords
    if ans.lower() == "all":
        idxs = [i for i, st, _ in rows if st in ("NEW", "INCOMPLETE")]
        return [episodes[i - 1] for i in idxs]
    if ans.lower() in ("new", "n"):
        idxs = [i for i, st, _ in rows if st == "NEW"]
        return [episodes[i - 1] for i in idxs]
    if ans.lower() in ("inc", "incomplete"):
        idxs = [i for i, st, _ in rows if st == "INCOMPLETE"]
        return [episodes[i - 1] for i in idxs]
    if ans.lower() in ("redo", "re", "redownload"):
        if not redownload:
            print("Use --redownload to allow forced re-downloads.")
            return []
        idxs = list(range(1, len(episodes) + 1))
        return [episodes[i - 1] for i in idxs]

    # Ranges / explicit numbers
    idxs = expand_tokens(ans.split())
    if not idxs:
        print("No valid selection.")
        return []
    return [episodes[i - 1] for i in idxs]

def merge_series_maps(into: Dict[str, SeriesSnapshot], new_map: Dict[str, SeriesSnapshot]) -> None:
    idx = {s.series_uuid or s.series_url: s for s in into.values()}
    for s in new_map.values():
        key = s.series_uuid or s.series_url
        if key in idx:
            existing = idx[key]
            for eid, ep in s.episodes.items():
                existing.episodes.setdefault(eid, ep)
            if existing.expected_total is None and s.expected_total is not None:
                existing.expected_total = s.expected_total
            if (not existing.series_title or existing.series_title == 'Unknown Series') and s.series_title:
                existing.series_title = s.series_title
        else:
            into[key] = s
            idx[key] = s

ROZHLAS_HOSTS = ("www.mujrozhlas.cz", "mujrozhlas.cz", "junior.rozhlas.cz", "dvojka.rozhlas.cz", "plus.rozhlas.cz")

def is_mujrozhlas_url(url: str) -> bool:
    return any(h in url for h in ROZHLAS_HOSTS)

def is_probably_episode_url(url: str) -> bool:
    """
    Heuristic: Rozhlas episode pages typically end with a long slug that includes an id.
    Example:
      https://www.mujrozhlas.cz/sobotni-drama/karel-...-o-nemocech
    We treat *program root* like /sobotni-drama as series; everything deeper is likely episode.
    """
    # Series root looks like /<program-slug>
    # Episode looks like /<program-slug>/<long-episode-slug...>
    parts = url.rstrip("/").split("/")
    return is_mujrozhlas_url(url) and len(parts) >= 5  # host + 1 + program + episode-slug

def series_root_from_url(url: str) -> str:
    """
    From episode URL return the program page (series root), e.g.
    https://www.mujrozhlas.cz/sobotni-drama/<episode-slug> -> https://www.mujrozhlas.cz/sobotni-drama
    """
    try:
        parts = url.rstrip("/").split("/")
        if len(parts) >= 5:
            return "/".join(parts[:4])
        return url
    except Exception:
        return url

from urllib.parse import urlparse

def _path_segments(u: str) -> list[str]:
    try:
        p = urlparse(u)
        segs = [s for s in p.path.strip("/").split("/") if s]
        return segs
    except Exception:
        return []

def program_slug_from_url(u: str) -> str | None:
    segs = _path_segments(u)
    return segs[0] if segs else None

def book_slug_from_url(u: str) -> str | None:
    segs = _path_segments(u)
    # For MujRozhlas program pages: /<program>/<book-or-series>/<episode-slug>...
    return segs[1] if len(segs) > 1 else None

def group_program_entries_by_book_slug(entries: list[dict]) -> dict[str, list[dict]]:
    """
    entries: list of episode dicts from discover_program_episodes()
    Groups by the second path segment (the book/series slug).
    """
    groups: dict[str, list[dict]] = {}
    for e in entries:
        u = (e.get("url") or "").strip()
        slug = book_slug_from_url(u) or "_unknown_"
        groups.setdefault(slug, []).append(e)
    return groups

# ===========================================================

# --- Classification & table ------------------------------------------------
@dataclass
class SeriesStatus:
    status: str  # Complete | Progress | Truncated | New
    local_count: int
    feed_count: int
    expected_total: Optional[int]
    reasons: List[str] = field(default_factory=list)

def classify_series(ss: SeriesSnapshot, db: Dict) -> SeriesStatus:
    db_key = ss.series_uuid or ss.series_url
    db_entry = db.get(db_key, {})
    local_eps = db_entry.get('episodes', {})
    local_count = len(local_eps)

    feed_indices = {e.playlist_index for e in ss.episodes.values() if e.playlist_index}
    truncated = (len(feed_indices) > 0 and 1 not in feed_indices and max(feed_indices) >= 3)

    expected = ss.expected_total
    feed_count = ss.total_in_feed

    if expected is not None:
        if local_count >= expected:
            return SeriesStatus('Complete', local_count, feed_count, expected)
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, expected, reasons=['Missing early episodes'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, expected)
        return SeriesStatus('New', 0, feed_count, expected)
    else:
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, None, reasons=['Missing early episodes'])
        if local_count > 0 and local_count >= feed_count and feed_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, None, reasons=['Complete as of current feed'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, None)
        return SeriesStatus('New', 0, feed_count, None)

class Ansi:
    RESET = "\033[0m"; BOLD = "\033[1m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; BLUE = "\033[34m"
STATUS_COLOR = {'Complete': Ansi.GREEN, 'Progress': Ansi.YELLOW, 'Truncated': Ansi.RED, 'New': Ansi.BLUE}

def print_status_table(series_list: List[Tuple[SeriesSnapshot, SeriesStatus]]):
    print()
    print(Ansi.BOLD + f"{'Series':66}  {'Status':10}  {'Local / Feed / Total'}" + Ansi.RESET)
    print('-' * 100)
    for ss, st in sorted(series_list, key=lambda x: (x[0].series_title or '').lower()):
        color = STATUS_COLOR.get(st.status, '')
        total_txt = str(st.expected_total) if st.expected_total is not None else '?'
        left = (ss.series_title or 'Unknown')[:66]
        right = f"{st.status:10}  {st.local_count:>3} / {st.feed_count:>3} / {total_txt:>5}"
        line = f"{left:66}  {color}{right}{Ansi.RESET}"
        print(line)
        if st.reasons:
            print(f"    - {'; '.join(st.reasons)}")
    print()

# --- Interactive picker ----------------------------------------------------
def prompt_select(prompt: str, valid: List[int], allow_blank=False) -> List[int]:
    print(prompt)
    raw = input("> ").strip()
    if allow_blank and raw == "":
        return []
    sel: List[int] = []
    for token in raw.replace(',', ' ').split():
        if '-' in token:
            a, b = token.split('-', 1)
            try:
                a = int(a); b = int(b)
                for i in range(min(a, b), max(a, b) + 1):
                    if i in valid: sel.append(i)
            except: pass
        else:
            try:
                i = int(token)
                if i in valid: sel.append(i)
            except: pass
    return sorted(set(sel))

# --- Download --------------------------------------------------------------

AUDIO_EXTS = {".m4a", ".m4b", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wav"}

def _find_downloaded_audio(temp_dir: str, audio_id: str) -> Optional[str]:
    """
    Look for a finished media file in temp_dir containing '[<id>].' in its name.
    Prefer non-.part files. If multiple, pick the newest.
    """
    candidates = []
    needle = f"[{audio_id}]"

    def add_if_audio(path: str):
        return (os.path.isfile(path) 
                and os.path.splitext(path)[1].lower() in AUDIO_EXTS)

    # Prefer finished audio (not .part)
    for name in os.listdir(temp_dir):
        if needle in name and not name.endswith(".part"):
            full = os.path.join(temp_dir, name)
            if add_if_audio(full):
                candidates.append(full)

    # Fallback: sometimes final file lingers as .part.<ext> (rare)
    if not candidates:
        for name in os.listdir(temp_dir):
            if needle in name and name.endswith(".part"):
                full = os.path.join(temp_dir, name)
                if add_if_audio(full):
                    candidates.append(full)
   
    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]

def progress_hook_factory(one_line_prefix: str):
    last_print = [0.0]
    def hook(d):
        if d.get('status') == 'downloading':
            p = d.get('_percent_str', '').strip()
            s = d.get('_speed_str', '').strip()
            eta = d.get('eta')
            now = time.time()
            if now - last_print[0] > 0.5:
                print(f"\r{one_line_prefix} {p} @ {s} ETA {eta}s", end='', flush=True)
                last_print[0] = now
        elif d.get('status') == 'finished':
            print(f"\r{one_line_prefix} 100% done.                           ")
    return hook

def download_episode_to_folder(ep: Episode, target_dir: str) -> bool:
    os.makedirs(target_dir, exist_ok=True)
    outtmpl = os.path.join(target_dir, "%(title).200B.%(ext)s")
    ydl_opts = {
        'quiet': True,
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'retries': 10,
        'fragment_retries': 10,
        'retry_sleep': 2,
        'socket_timeout': 30,
        'http_headers': {'User-Agent': 'Mozilla/5.0 audiobiblio/yt-dlp'},
        'progress_hooks': [progress_hook_factory(f"Downloading {ep.title[:40]!r}")],
        'postprocessors': [{'key': 'FFmpegCopyStream'}],
        'paths': {'home': target_dir},
    }
    if not ep.url:
        return False
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([ep.url])
        return True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\nDownload failed for {ep.title}: {e}")
        return False

def maybe_run_tag_fixer(output_folder: str, args):
    if not getattr(args, "tag_fix", False):
        return
    cmd = [sys.executable, "-m", "audiobiblio.tag_fixer", output_folder]
    if getattr(args, "tf_apply", False):
        cmd.append("--force")
    if getattr(args, "tf_rename", False):
        cmd.append("--rename")
    if getattr(args, "tf_renumber", False):
        cmd.append("--renumber")
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"[tag_fixer] skipped: {e}")

def download_one_episode(url: str):
    """
    Download a single episode URL with yt-dlp using YDL_DL_OPTS.
    Returns (ok, filepath_or_None, info_dict_or_None).
    """
    try:
        with YoutubeDL(YDL_DL_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        print(f"  ! Download failed: {e}")
        return False, None, None

    # Refuse playlist inputs defensively (even though noplaylist=True)
    if isinstance(info, dict) and info.get("_type") == "playlist":
        print("  ! URL resolved to a playlist; per-episode downloader refuses playlist inputs.")
        return False, None, info

    ep_id = str(info.get("id") or "").strip() if isinstance(info, dict) else ""
    filepath = _find_downloaded_audio(DIR_DOWNLOADING, ep_id) if ep_id else None

    # Fallback: use yt-dlp’s direct filename but only if it’s actually audio
    if not filepath and isinstance(info, dict):
        fn = info.get("_filename")
        if fn and os.path.exists(fn) and Path(fn).suffix.lower() in AUDIO_EXTS:
            filepath = fn

    if not filepath and ep_id:
        print("  ! Could not locate downloaded audio by id; leaving as-is.")

    return True, filepath, info

# --- State check in _complete ----------------------------------------------

def _episode_state_in_complete(ep_id: str, title: str | None = None) -> str:
    """
    Inspect _complete/ to determine if an episode with ep_id (or title) is:
    - "DONE": audio present AND sidecars present (.info.json + .description + thumbnail)
    - "INCOMPLETE": audio present but any sidecar missing
    - "NEW": nothing found
    """
    if not ep_id and not title:
        return "NEW"

    audio_path = None
    if os.path.isdir(DIR_COMPLETE):
        for p in Path(DIR_COMPLETE).glob("*"):
            if not p.is_file():
                continue
            n = p.name
            if ep_id and f"[{ep_id}]" in n:
                audio_path = p
                break
            if not audio_path and title and title in n:
                audio_path = p

    if not audio_path:
        return "NEW"

    base = audio_path.with_suffix("")  # strip .ext, keep [id]
    info_json = base.with_suffix(base.suffix + ".info.json")
    descr     = base.with_suffix(base.suffix + ".description")
    thumb_ok  = any(base.with_suffix(base.suffix + ext).exists()
                    for ext in (".jpg", ".jpeg", ".png", ".webp"))

    if info_json.exists() and descr.exists() and thumb_ok:
        return "DONE"
    else:
        return "INCOMPLETE"

def download_batch(urls: list[str], args) -> None:
    """
    Download a batch of episode URLs sequentially.
    For each URL:
      - quick preflight: discover ep_id/title and current state in _complete
      - skip DONE unless --redownload
      - skip INCOMPLETE unless --redownload-missing
      - download with yt-dlp
      - move audio + sidecars into _complete via _finalize_move(...)
      - optionally run tag_fixer
    """
    if not urls:
        print("Nothing to download.")
        return

    print(f"\nStarting downloads ({len(urls)} episode(s))...")
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

        if ep_id or ep_title:
            state = _episode_state_in_complete(ep_id, ep_title)
            if state == "DONE" and not getattr(args, "redownload", False):
                print(f"  ↷ Skip (already downloaded): {ep_id or ep_title[:40]}")
                logging.info(f"SKIP DONE: {ep_id or ep_title}")
                continue
            if state == "INCOMPLETE" and not getattr(args, "redownload_missing", False):
                print(f"  ↷ Skip (incomplete; use --redownload-missing to re-fetch sidecars): {ep_id or ep_title[:40]}")
                logging.info(f"SKIP INCOMPLETE: {ep_id or ep_title}")
                continue

        # DOWNLOAD
        ok, src_file, info = download_one_episode(url)
        if not ok:
            continue

        if src_file:
            # Move audio + sidecars to structured _complete path
            final_audio = _finalize_move(src_file, info or {})

            # TAG FIX (optional)
            if getattr(args, "tag_fix", False):
                overrides_path = _write_overrides_json_for_tagger(info or {}, final_audio)
                _run_tag_fixer_on_file(
                    final_audio,
                    auto_apply=getattr(args, "tf_apply", False),
                    auto_rename=getattr(args, "tf_rename", False),
                    auto_renumber=getattr(args, "tf_renumber", False),
                    overrides_path=overrides_path,
                )

    print("\nAll selected downloads processed.")

# --- Main ------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Discover and selectively download from MujRozhlas")

    # Modes / behavior
    ap.add_argument("--download", action="store_true", help="Enable download flow (otherwise report only)")
    ap.add_argument("--unattended", action="store_true", help="No interactive prompts; process exactly the URLs")

    # Semi-robot default ON, with an OFF switch
    ap.add_argument(
        "--semi-robot", dest="semi_robot", action="store_true", default=True,
        help="Interactive picker with sensible defaults. ENTER = download all suggested NEW "
             "(and INCOMPLETE if --redownload-missing). On by default."
    )
    ap.add_argument("--no-semi-robot", dest="semi_robot", action="store_false", help=argparse.SUPPRESS)

    # Re-download behavior
    ap.add_argument("--redownload-missing", action="store_true",
                    help="Also suggest re-downloading episodes with missing sidecars (INCOMPLETE).")
    ap.add_argument("--redownload", action="store_true",
                    help="Force re-download even if audio and sidecars are already in _complete.")

    # Download/runtime options
    ap.add_argument("--sequential", action="store_true", help="Disable parallelism (reserved for future)")
    ap.add_argument("--max-workers", type=int, default=4, help="Parallel workers (reserved for future)")
    ap.add_argument("--throttle", type=float, default=1.0, help="Seconds to sleep between discovery requests")

    # URLs
    ap.add_argument("--url", action="append", help="URL to process (repeatable)")
    ap.add_argument("positional_urls", nargs="*", help="URLs (same as --url)")

    # Tag fixer
    ap.add_argument("--tag-fix", action="store_true", help="Run tag_fixer on the final folder (if available)")
    ap.add_argument("--tf-apply", action="store_true", help="Tag-fixer: auto-apply after suggestions")
    ap.add_argument("--tf-rename", action="store_true", help="Tag-fixer: rename files from tags")
    ap.add_argument("--tf-renumber", action="store_true", help="Tag-fixer: renumber tracks")

    # Misc
    ap.add_argument("--version", action="store_true", help="Print version and exit")
    ap.add_argument("--debug", action="store_true", help="Verbose console logging in addition to log file")

    args = ap.parse_args()

    # --version early exit
    if args.version:
        print("audioloader version 0.3.0")
        return 0

    # Optional console logging when --debug
    if args.debug:
        _console = logging.StreamHandler()
        _console.setLevel(logging.DEBUG)
        _console.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logging.getLogger().addHandler(_console)
        logging.getLogger().setLevel(logging.DEBUG)
        log("Debug logging enabled")

    # ------------------------------------------------------------------
    # Build target URL list (flatten --url + positional)
    # ------------------------------------------------------------------
    input_urls: list[str] = []
    if args.url:
        input_urls.extend(args.url)
    if args.positional_urls:
        input_urls.extend(args.positional_urls)

    # ------------------------------------------------------------------
    # If user provided URLs → handle them and exit (skip global crawl)
    # ------------------------------------------------------------------
    if input_urls:
        log(f"Using {len(input_urls)} user-provided URL(s); skipping websites_mujrozhlas.json")
        db = load_db()

        for u in input_urls:
            print(f"Discovering: {u}")

            # Normalize non-mujrozhlas hosts → try to resolve/redirect once
            if not is_mujrozhlas_url(u):
                try:
                    u_resolved = resolve_to_mujrozhlas(u)  # your helper; return u if unchanged
                except NameError:
                    u_resolved = u  # if you don’t have this function, just fall back
                if u_resolved != u:
                    print(f"Resolved to MujRozhlas: {u_resolved}")
                u = u_resolved

            # Episode vs program decision
            if is_probably_episode_url(u):
                # 1) Read the episode title (rich is fine for one page)
                try:
                    info = ydl_extract_rich(u)
                    ep_title = (info.get("title") or "").strip()
                except Exception as e:
                    print(f"Could not read episode: {e}")
                    continue

                # 2) Program root (first path segment)
                program_url = program_root_from_url(u)
                if not program_url:
                    print("Could not infer program root from URL.")
                    continue

                # 3) Discover ALL entries on program page
                episodes = discover_program_episodes(program_url)
                if not episodes:
                    print("No episodes found on program page.")
                    continue

                # 4) Group by title-derived series key; pick best match for our episode
                groups = group_program_entries_by_series_key(episodes)
                picked = best_matching_group(groups, ep_title)
                if not picked:
                    print("No matching series group; showing all episodes.")
                    chosen = prompt_select_episodes(
                        episodes,
                        preselect=None if args.unattended else None,
                        semi_robot=args.semi_robot,
                        redownload_missing=args.redownload_missing,
                        redownload=args.redownload,
                    )
                    if not chosen:
                        print("No selection made; skipping.")
                        continue
                    download_queue = [ep["url"] for ep in chosen if ep.get("url")]
                    download_batch(download_queue, args)
                    continue

                primary_key, primary_list = picked

                # 5) Headline for the primary series
                pretty_name = primary_key.title() if primary_key != "_unknown_" else "Unknown series"
                print("\nSeries detected from your link:")
                print(f"  {pretty_name} — {len(primary_list)} episode(s)")

                # Preselect the pasted episode if present
                pre = []
                for idx, ep in enumerate(primary_list, start=1):
                    if (ep.get("url") or "").rstrip("/") == u.rstrip("/"):
                        pre = [idx]
                        break

                chosen_primary = prompt_select_episodes(
                    primary_list,
                    preselect=None if args.unattended else pre,
                    semi_robot=args.semi_robot,
                    redownload_missing=args.redownload_missing,
                    redownload=args.redownload,
                )

                # 6) Optional: show other series in this program
                others = [(k, v) for k, v in groups.items() if k != primary_key]
                chosen_more = []
                if others and not args.unattended:
                    print("\nOther series in this program:")
                    def _p(k: str) -> str: return (k.title() if k != "_unknown_" else "Unknown")
                    others_sorted = sorted(others, key=lambda kv: _p(kv[0]).lower())
                    for i, (k, lst) in enumerate(others_sorted, start=1):
                        have = sum(1 for e in lst if episode_looks_downloaded(e))
                        print(f"  {i:2d}. {_p(k):<42} total: {len(lst):>3}  downloaded: {have:>3}")
                    ans = input("\nAlso pick from another series? Enter its number, or ENTER to skip: ").strip()
                    if ans.isdigit():
                        idx = int(ans)
                        if 1 <= idx <= len(others_sorted):
                            _, lst = others_sorted[idx-1]
                            print(f"\nSeries: {_p(others_sorted[idx-1][0])} — {len(lst)} episode(s)")
                            chosen_more = prompt_select_episodes(
                                lst,
                                preselect=None,
                                semi_robot=args.semi_robot,
                                redownload_missing=args.redownload_missing,
                                redownload=args.redownload,
                            )

                # 7) Merge selections and download
                chosen = (chosen_primary or []) + (chosen_more or [])
                if not chosen:
                    print("No selection made; skipping.")
                    continue

                download_queue = [ep["url"] for ep in chosen if ep.get("url")]
                download_batch(download_queue, args)
                continue  # proceed to next input URL

            # Otherwise you pasted a PROGRAM page → list all episodes directly
            episodes = discover_program_episodes(u)
            if episodes:
                chosen = prompt_select_episodes(
                    episodes,
                    preselect=list(range(1, len(episodes)+1)) if args.unattended else None,
                    semi_robot=args.semi_robot,
                    redownload_missing=args.redownload_missing,
                    redownload=args.redownload,
                )
                if not chosen:
                    print("No selection made; skipping.")
                    continue

                urls = [ep["url"] for ep in chosen if ep.get("url")]
                download_batch(urls, args)
            else:
                # Fallback: treat as a channel (bigger discovery)
                s_map = gather_series_from_channel(u)
                classified = []
                for ss in s_map.values():
                    st = classify_series(ss, db)
                    classified.append((ss, st))
                print_status_table(classified)

                if args.download and not args.unattended:
                    series_list = sorted(s_map.values(), key=lambda s: (s.series_title or "").lower())
                    if series_list:
                        first = series_list[0]
                        st = classify_series(first, db)
                        print()
                        print(f" 1. {first.series_title}  [{st.status}]  "
                              f"{st.local_count}/{st.feed_count}/{st.expected_total or '?'}")
                        ans = input("Select series to download (e.g. 1 or ENTER to cancel): ").strip()
                        if ans == "1":
                            urls = [ep.url for ep in first.episodes.values() if ep.url]
                            download_batch(urls, args)

        # We are done with direct URLs
        return 0

if __name__ == '__main__':
    sys.exit(main())