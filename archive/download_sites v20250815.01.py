#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MujRozhlas downloader — Phase 1: Discovery + Status Report (no downloads yet)

Goals in this phase:
- Read channel URLs from websites_mujrozhlas.json
- Use yt_dlp in extract-only mode to build a hierarchy: Channel -> Series -> Episodes
- Identify series by a stable UUID when available; otherwise fall back to a deterministic key
- Merge the same series discovered under different channels
- Compare with local DB (episodes_db.json)
- Classify series into: Complete, Progress, Truncated, New
- Print a color-coded summary table
- Prepare folder targets: media/_downloading, media/_progress, media/_complete, media/_truncated

Download logic will be added in Phase 2 (episode-at-a-time with immediate tagging+moving).
This script is safe to run now; it DOES NOT download media.

It supports both run modes:
- Direct:    python3 download_sites.py
- Package:   python3 -m mujrozhlas.download_sites
"""
from __future__ import annotations
import json
import os
import sys
import subprocess
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Flexible imports (standalone vs package)
try:
    from .utils import (
        strip_diacritics,
        sanitize_filename,
        clean_tag_text,
        safe_int,
        safe_year,
        join_nonempty,
        extract_station_code,
    )
    from .metadata import enrich_metadata  # not used yet in Phase 1, kept for Phase 2
except Exception:  # pragma: no cover
    from utils import (
        strip_diacritics,
        sanitize_filename,
        clean_tag_text,
        safe_int,
        safe_year,
        join_nonempty,
        extract_station_code,
    )
    from metadata import enrich_metadata  # noqa

from yt_dlp import YoutubeDL

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

# --- Logging ---------------------------------------------------------------
logging.basicConfig(
    filename=os.path.join(BASE_DIR, 'download.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)
log = lambda m: (print(m), logging.info(m))

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
    series_uuid: str  # Stable identifier when available
    series_title: str
    series_url: str
    channel_url: str
    channel_title: Optional[str] = None
    total_in_feed: int = 0
    expected_total: Optional[int] = None  # If the site exposes a total
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


# --- yt-dlp helpers --------------------------------------------------------

YDL_COMMON_OPTS = {
    'quiet': True,
    'skip_download': True,  # ensure no download actions happen
    'extract_flat': False,  # we want rich metadata where possible
}


def ydl_extract(url: str) -> dict:
    """Extract info for a URL without downloading media."""
    with YoutubeDL(YDL_COMMON_OPTS) as ydl:
        return ydl.extract_info(url, download=False)


# --- Discovery logic -------------------------------------------------------

def derive_series_url_from_episode(ep_url: str) -> str:
    """Heuristic: series page is the episode URL without the final path segment."""
    try:
        parts = ep_url.rstrip('/').split('/')
        if len(parts) > 3:
            return '/'.join(parts[:-1])
        return ep_url
    except Exception:
        return ep_url


def gather_series_from_channel(channel_url: str) -> Dict[str, SeriesSnapshot]:
    """Return mapping: series_key -> SeriesSnapshot for a given channel."""
    data = ydl_extract(channel_url)
    channel_title = data.get('title') or data.get('playlist_title')

    series_map: Dict[str, SeriesSnapshot] = {}

    entries = data.get('entries') or []
    for entry in entries:
        # Some entries might be episodes directly
        ep_id = str(entry.get('id')) if entry.get('id') is not None else None
        ep_title = entry.get('title') or ''
        ep_url = entry.get('webpage_url') or entry.get('url') or channel_url
        ep_idx = entry.get('playlist_index')
        ep_dur = entry.get('duration')
        ep_upd = entry.get('upload_date')

        # Series identity — prefer playlist/series/season identifiers from metadata
        series_uuid = (
            str(entry.get('series_id') or entry.get('season_id') or entry.get('playlist_id') or '')
        )
        # Series title candidates
        series_title = (
            entry.get('series') or entry.get('season') or entry.get('playlist') or entry.get('playlist_title')
        )
        # Fallback series URL from episode URL
        series_url = derive_series_url_from_episode(ep_url)

        # If we don't have a series_uuid yet, try to extract from the series page itself
        # (costly, but only once per series_url key)
        series_key = series_uuid or series_url
        if series_key not in series_map:
            # Try to enrich by scraping the series page to get stable UUID & total expected
            try:
                sdata = ydl_extract(series_url)
                s_uuid = str(sdata.get('id') or '')
                s_title = sdata.get('title') or series_title or 'Unknown Series'
                expected_total = sdata.get('playlist_count')
                # Build the snapshot (episodes filled below)
                ss = SeriesSnapshot(
                    series_uuid=s_uuid or series_key,
                    series_title=s_title,
                    series_url=series_url,
                    channel_url=channel_url,
                    channel_title=channel_title,
                    total_in_feed=0,
                    expected_total=expected_total,
                )
            except Exception:
                # Fallback when series page can't be extracted
                ss = SeriesSnapshot(
                    series_uuid=series_key,
                    series_title=series_title or 'Unknown Series',
                    series_url=series_url,
                    channel_url=channel_url,
                    channel_title=channel_title,
                    total_in_feed=0,
                    expected_total=None,
                )
            series_map[series_key] = ss

        ss = series_map[series_key]
        if ep_id:
            ss.episodes[ep_id] = Episode(
                id=ep_id,
                title=ep_title or f"Episode {ep_idx or '?'}",
                playlist_index=ep_idx if ep_idx is not None else None,
                url=ep_url,
                duration=ep_dur,
                upload_date=ep_upd,
            )

    # Finalize counts
    for ss in series_map.values():
        ss.total_in_feed = len(ss.episodes)
    return series_map


def merge_series_maps(into: Dict[str, SeriesSnapshot], new_map: Dict[str, SeriesSnapshot]) -> None:
    """Merge series discovered from another channel into the global map (by UUID if present)."""
    # Build index by UUID (or fallback key)
    index: Dict[str, SeriesSnapshot] = {}
    for s in into.values():
        key = s.series_uuid or s.series_url
        index[key] = s

    for s in new_map.values():
        key = s.series_uuid or s.series_url
        if key in index:
            existing = index[key]
            # Merge episodes
            for eid, ep in s.episodes.items():
                existing.episodes.setdefault(eid, ep)
            # Prefer a non-empty expected_total
            if existing.expected_total is None and s.expected_total is not None:
                existing.expected_total = s.expected_total
            # Keep the most descriptive title
            if (not existing.series_title or existing.series_title == 'Unknown Series') and s.series_title:
                existing.series_title = s.series_title
        else:
            into[key] = s
            index[key] = s


# --- Classification --------------------------------------------------------

@dataclass
class SeriesStatus:
    status: str  # 'Complete' | 'Progress' | 'Truncated' | 'New'
    local_count: int
    feed_count: int
    expected_total: Optional[int]
    reasons: List[str] = field(default_factory=list)


def classify_series(ss: SeriesSnapshot, db: Dict) -> SeriesStatus:
    # DB uses series_uuid as key; fall back to series_url
    db_key = ss.series_uuid or ss.series_url
    db_entry = db.get(db_key, {})
    local_eps = db_entry.get('episodes', {})
    local_count = len(local_eps)

    feed_indices = {e.playlist_index for e in ss.episodes.values() if e.playlist_index}
    # Heuristic: truncated if index "1" is missing but a higher index exists
    truncated = (len(feed_indices) > 0 and 1 not in feed_indices and max(feed_indices) >= 3)

    expected = ss.expected_total
    feed_count = ss.total_in_feed

    if expected is not None:
        if local_count >= expected:
            return SeriesStatus('Complete', local_count, feed_count, expected)
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, expected, reasons=['Missing early episodes in feed'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, expected)
        return SeriesStatus('New', 0, feed_count, expected)
    else:
        # No expected total — rely on feed vs local and truncation heuristic
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, None, reasons=['Missing early episodes in feed'])
        if local_count > 0 and local_count >= feed_count and feed_count > 0:
            # Might be complete *as of now*
            return SeriesStatus('Progress', local_count, feed_count, None, reasons=['Complete as of current feed'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, None)
        return SeriesStatus('New', 0, feed_count, None)


# --- Pretty printing -------------------------------------------------------

class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"

STATUS_COLOR = {
    'Complete': Ansi.GREEN,
    'Progress': Ansi.YELLOW,
    'Truncated': Ansi.RED,
    'New': Ansi.BLUE,
}


def print_status_table(series_list: List[Tuple[SeriesSnapshot, SeriesStatus]]):
    # Header
    print()
    print(Ansi.BOLD + f"{'Series':66}  {'Status':10}  {'Local / Feed / Total'}" + Ansi.RESET)
    print('-' * 100)
    # Rows
    for ss, st in sorted(series_list, key=lambda x: x[0].series_title.lower()):
        color = STATUS_COLOR.get(st.status, '')
        total_txt = str(st.expected_total) if st.expected_total is not None else '?'
        left = (ss.series_title or 'Unknown')[:66]
        right = f"{st.status:10}  {st.local_count:>3} / {st.feed_count:>3} / {total_txt:>5}"
        line = f"{left:66}  {color}{right}{Ansi.RESET}"
        print(line)
        if st.reasons:
            print(f"    - {'; '.join(st.reasons)}")
    print()


# --- Main ------------------------------------------------------------------

def main(report_only: bool = True) -> int:
    # Load DB
    db = load_db()

    # Load channel list
    if not os.path.exists(SERIES_FILE):
        print(f"Missing {SERIES_FILE}. Please create it with a list of channel URLs.")
        return 2
    with open(SERIES_FILE, 'r', encoding='utf-8') as f:
        channels = json.load(f)
    if not isinstance(channels, list):
        print("websites_mujrozhlas.json must be a JSON list of channel URLs")
        return 2

    # Discover all series via all channels
    global_series: Dict[str, SeriesSnapshot] = {}
    for ch_url in channels:
        try:
            log(f"Discovering channel: {ch_url}")
            s_map = gather_series_from_channel(ch_url)
            merge_series_maps(global_series, s_map)
        except Exception as e:
            log(f"Channel discovery error for {ch_url}: {e}")

    # Classify each series and print report
    classified: List[Tuple[SeriesSnapshot, SeriesStatus]] = []
    for ss in global_series.values():
        st = classify_series(ss, db)
        classified.append((ss, st))

    print_status_table(classified)

    # NOTE: In Phase 1 we stop here (report only)
    return 0


if __name__ == '__main__':
    # Always run in report-only mode for this phase
    sys.exit(main(report_only=True))
