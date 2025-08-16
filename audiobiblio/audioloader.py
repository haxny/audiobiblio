#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audioloader.py

MujRozhlas FULL runner:
- Interactive by default (ask before downloading; let you pick series)
- Unattended mode with --unattended
- Priority single URL with --url (goes first, even if not in JSON)
- Parallel downloads by default (threaded); --sequential to force single-thread
- Discovery → Report → (optional) Download → (optional) Post-process tags with tag_fixer.py
- Robust per-album folders; per-album .nfo logs with date-stamped files
- DB-based dedupe: never re-download same episode unless size/hash/mtime changes

Run examples
------------
# interactive status only
python3 audioloader.py

# interactive + then download selected series
python3 audioloader.py --download

# unattended: download everything new, then apply tag fixes & rename
python3 audioloader.py --download --unattended --tag-fix --tf-apply --tf-rename --tf-renumber

# urgent one-off URL first, then normal queue
python3 audioloader.py --download --url https://www.mujrozhlas.cz/velka-pohadka --tag-fix

Notes
-----
- Requires yt_dlp
- Optional: mutagen + exiftool (used by tag_fixer.py)
"""

from __future__ import annotations
import argparse
import concurrent.futures
import json
import logging
import os
import re
import shutil
import sys
import threading
import time

from .__version__ import __version__
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

# --------------------------
# Paths & constants
# --------------------------
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_FILE    = os.path.join(BASE_DIR, 'episodes_db.json')
SERIES_FILE= os.path.join(BASE_DIR, 'websites_mujrozhlas.json')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DIR_DOWNLOADING = os.path.join(MEDIA_ROOT, '_downloading')
DIR_PROGRESS    = os.path.join(MEDIA_ROOT, '_progress')
DIR_COMPLETE    = os.path.join(MEDIA_ROOT, '_complete')
DIR_TRUNCATED   = os.path.join(MEDIA_ROOT, '_truncated')

for d in (MEDIA_ROOT, DIR_DOWNLOADING, DIR_PROGRESS, DIR_COMPLETE, DIR_TRUNCATED):
    os.makedirs(d, exist_ok=True)

# station map (host -> code)
STATIONS = {
    "radiozurnal.rozhlas.cz": "CRo1",
    "dvojka.rozhlas.cz": "CRo2",
    "vltava.rozhlas.cz": "CRo3",
    "plus.rozhlas.cz": "CRo+",
    "junior.rozhlas.cz": "CRoJun",
    "www.radiojunior.cz": "CRoJun",
    "wave.rozhlas.cz": "CRoW",
    "d-dur.rozhlas.cz": "CRoDdur",
    "jazz.rozhlas.cz": "CRoJazz",
    "pohoda.rozhlas.cz": "CRoPohoda",
    "radio.cz": "CRoInt",
    # locals
    "brno.rozhlas.cz": "CRoBrno",
    "budejovice.rozhlas.cz": "CRoCB",
    "hradec.rozhlas.cz": "CRoHK",
    "vary.rozhlas.cz": "CRoKV",
    "liberec.rozhlas.cz": "CRoLib",
    "olomouc.rozhlas.cz": "CRoOL",
    "ostrava.rozhlas.cz": "CRoOV",
    "pardubice.rozhlas.cz": "CRoPard",
    "plzen.rozhlas.cz": "CRoPlz",
    "praha.rozhlas.cz": "CRoPRG",
    "region.rozhlas.cz": "CRoRegion",
    "sever.rozhlas.cz": "CRoSever",
    "vysocina.rozhlas.cz": "CRoVys",
    "zlin.rozhlas.cz": "CRoZL",
}

# logging
LOG_PATH = os.path.join(BASE_DIR, 'download.log')
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    encoding='utf-8'
)
def log(msg: str):
    print(msg)
    logging.info(msg)

# simple ANSI
class C:
    R="\033[31m"; G="\033[32m"; Y="\033[33m"; B="\033[34m"; M="\033[35m"; C="\033[36m"; W="\033[37m"; X="\033[0m"; BD="\033[1m"

# --------------------------
# DB helpers
# --------------------------
def load_db() -> Dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_db(db: Dict) -> None:
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

# --------------------------
# Utils
# --------------------------
_ilock = threading.Lock()

def strip_diacritics(s: str) -> str:
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def sanitize_filename(s: str) -> str:
    s = strip_diacritics(s)
    s = re.sub(r'[<>:"/\\|?*]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def now_date_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")

def station_code_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    # map mujrozhlas program pages back to station via sibling site if known
    # if no direct match, best-effort keep host's first label
    for k,v in STATIONS.items():
        if host.endswith(k):
            return v
    return "CRo"

def ensure_album_nfo_dir(album_dir: str) -> str:
    # per-album .nfo directory for logs/json
    nfo_dir = os.path.join(album_dir, ".nfo")
    os.makedirs(nfo_dir, exist_ok=True)
    return nfo_dir

def append_album_log(album_dir: str, line: str):
    nfo = ensure_album_nfo_dir(album_dir)
    path = os.path.join(nfo, f"log_{now_date_stamp()}.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")

# --------------------------
# yt-dlp helpers
# --------------------------
YDL_EXTRACT = {
    'quiet': True,
    'skip_download': True,
    'extract_flat': False,
}

def ydl_extract(url: str) -> dict:
    with YoutubeDL(YDL_EXTRACT) as ydl:
        return ydl.extract_info(url, download=False)

# progress hook (one-line)
_progress_lines = {}
def progress_hook(d):
    # compress chunk spam into one line per file
    filename = d.get('filename')
    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)
        sp = d.get('_speed_str', '')
        pr = d.get('_percent_str', '').strip()
        eta = d.get('_eta_str', '').strip()
        line = f"↓ {os.path.basename(filename)} — {pr} {sp} ETA {eta}"
        with _ilock:
            _progress_lines[filename] = line
        # print single carriage-returning line
        sys.stdout.write("\r" + line[:120])
        sys.stdout.flush()
    elif d['status'] == 'finished':
        with _ilock:
            _progress_lines.pop(filename, None)
        sys.stdout.write("\r" + (" " * 120) + "\r")
        sys.stdout.flush()
        print(f"{C.G}✓ done:{C.X} {os.path.basename(filename)}")

# downloader template
def make_ydl_downloader(outtmpl: str) -> YoutubeDL:
    opts = {
        'outtmpl': outtmpl,
        'quiet': False,
        'noprogress': True,  # we'll show our own
        'progress_hooks': [progress_hook],
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'embedmetadata': True,
        'writeinfojson': True,
        'nooverwrites': False,
        'concurrent_fragment_downloads': 5,
        # slow servers -> be nice but resilient
        'sleep_interval': 2,
        'max_sleep_interval': 5,
    }
    return YoutubeDL(opts)

# --------------------------
# Data models
# --------------------------
@dataclass
class Episode:
    id: str
    title: str
    url: str
    playlist_index: Optional[int] = None
    upload_date: Optional[str] = None
    ext: Optional[str] = None

@dataclass
class SeriesSnapshot:
    key: str              # stable: playlist/series id; else fallback to series URL
    title: str
    series_url: str
    channel_url: str
    channel_title: Optional[str] = None
    expected_total: Optional[int] = None
    episodes: Dict[str, Episode] = field(default_factory=dict)

# --------------------------
# Discovery
# --------------------------
def derive_series_url(ep_url: str) -> str:
    parts = ep_url.rstrip('/').split('/')
    return '/'.join(parts[:-1]) if len(parts) > 3 else ep_url

def gather_series_from_channel(channel_url: str) -> Dict[str, SeriesSnapshot]:
    out: Dict[str, SeriesSnapshot] = {}
    data = ydl_extract(channel_url)
    chan_title = data.get('title') or data.get('playlist_title')
    for e in (data.get('entries') or []):
        ep_id = str(e.get('id') or '')
        ep_title = e.get('title') or ''
        ep_url = e.get('webpage_url') or e.get('url') or channel_url
        ep_idx = e.get('playlist_index')
        ep_upd = e.get('upload_date')
        ep_ext = e.get('ext')

        s_uuid = str(e.get('series_id') or e.get('season_id') or e.get('playlist_id') or '')
        s_title = e.get('series') or e.get('season') or e.get('playlist') or e.get('playlist_title')
        s_url   = derive_series_url(ep_url)
        s_key = s_uuid or s_url

        if s_key not in out:
            # try to enrich with series page
            try:
                sdata = ydl_extract(s_url)
                _uuid = str(sdata.get('id') or '') or s_key
                _title= sdata.get('title') or s_title or 'Unknown Series'
                _count= sdata.get('playlist_count')
                out[s_key] = SeriesSnapshot(
                    key=_uuid, title=_title, series_url=s_url,
                    channel_url=channel_url, channel_title=chan_title,
                    expected_total=_count or None
                )
            except Exception:
                out[s_key] = SeriesSnapshot(
                    key=s_key, title=s_title or 'Unknown Series',
                    series_url=s_url, channel_url=channel_url,
                    channel_title=chan_title
                )

        if ep_id:
            out[s_key].episodes.setdefault(ep_id, Episode(
                id=ep_id, title=ep_title, url=ep_url,
                playlist_index=ep_idx, upload_date=ep_upd, ext=ep_ext
            ))
    return out

def merge_series_maps(base: Dict[str, SeriesSnapshot], new: Dict[str, SeriesSnapshot]):
    # merge by .key (uuid if possible)
    for k, ss in new.items():
        if ss.key in base:
            tgt = base[ss.key]
            # episodes
            for eid, ep in ss.episodes.items():
                tgt.episodes.setdefault(eid, ep)
            if tgt.expected_total is None and ss.expected_total is not None:
                tgt.expected_total = ss.expected_total
            if (not tgt.title or tgt.title == 'Unknown Series') and ss.title:
                tgt.title = ss.title
        else:
            base[ss.key] = ss

# --------------------------
# Classification for report
# --------------------------
@dataclass
class SeriesStatus:
    status: str
    local_count: int
    feed_count: int
    expected_total: Optional[int]
    reasons: List[str] = field(default_factory=list)

def classify_series(ss: SeriesSnapshot, db: Dict) -> SeriesStatus:
    db_entry = db.get(ss.key, {})
    local_eps = db_entry.get('episodes', {})
    local_count = len(local_eps)

    feed_count = len(ss.episodes)
    indices = {e.playlist_index for e in ss.episodes.values() if e.playlist_index}
    truncated = (indices and 1 not in indices and max(indices) >= 3)

    exp = ss.expected_total
    if exp is not None:
        if local_count >= exp:
            return SeriesStatus('Complete', local_count, feed_count, exp)
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, exp, reasons=['Missing early episodes in feed'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, exp)
        return SeriesStatus('New', 0, feed_count, exp)
    else:
        if truncated:
            return SeriesStatus('Truncated', local_count, feed_count, None, reasons=['Missing early episodes in feed'])
        if local_count >= feed_count and feed_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, None, reasons=['Complete as of current feed'])
        if local_count > 0:
            return SeriesStatus('Progress', local_count, feed_count, None)
        return SeriesStatus('New', 0, feed_count, None)

def print_status_table(rows: List[Tuple[SeriesSnapshot, SeriesStatus]]):
    print()
    print(C.BD + f"{'Series':66}  {'Status':10}  {'Local/Feed/Total'}" + C.X)
    print('-' * 100)
    for ss, st in sorted(rows, key=lambda x: x[0].title.lower()):
        color = {'Complete':C.G,'Progress':C.Y,'Truncated':C.R,'New':C.C}.get(st.status, C.W)
        total_txt = str(st.expected_total) if st.expected_total is not None else '?'
        left = (ss.title or 'Unknown')[:66]
        right = f"{st.status:10}  {st.local_count:>3}/{st.feed_count:>3}/{total_txt:>5}"
        print(f"{left:66}  {color}{right}{C.X}")
        for r in st.reasons:
            print(f"    - {r}")
    print()

# --------------------------
# Folder layout + filenames
# --------------------------
def album_root_for_series(ss: SeriesSnapshot) -> str:
    # Station code in parentheses; base by series slug/title
    st = station_code_for_url(ss.channel_url)
    base = sanitize_filename(ss.title or os.path.basename(ss.series_url.strip('/')))
    return os.path.join(MEDIA_ROOT, f"{base} ({st})")

def pick_episode_filename(ss: SeriesSnapshot, ep: Episode, ext: str) -> str:
    # Basic collision-free filename, legible, diacritics stripped
    # We'll leave the fancy author/year/reader naming to tag_fixer later.
    series = sanitize_filename(ss.title or 'Unknown')
    date = (ep.upload_date or '00000000')
    title = sanitize_filename(ep.title or ep.id)
    return f"{series} {date} {title}{ext}"

# --------------------------
# Download core
# --------------------------
def already_have(db: Dict, ss: SeriesSnapshot, ep: Episode) -> bool:
    row = db.get(ss.key, {}).get('episodes', {}).get(ep.id)
    return bool(row and os.path.isfile(row.get('path', '')))

def update_db_episode(db: Dict, ss: SeriesSnapshot, ep: Episode, media_path: str, info_path: str, size: int):
    db.setdefault(ss.key, {'series': ss.title, 'series_url': ss.series_url, 'episodes': {}})
    db[ss.key]['episodes'][ep.id] = {
        'title': ep.title,
        'url': ep.url,
        'path': media_path,
        'info': info_path,
        'size': size,
        'last_seen': datetime.now().strftime("%Y-%m-%d"),
    }

def download_episode(ss: SeriesSnapshot, ep: Episode, db: Dict) -> Optional[str]:
    album_dir = album_root_for_series(ss)
    os.makedirs(album_dir, exist_ok=True)
    nfo_dir = ensure_album_nfo_dir(album_dir)

    # outtmpl goes to _downloading to avoid partials mixing with final layout
    tmp_dir = os.path.join(album_dir, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # extension from metadata (fallback .m4a)
    ext = f".{ep.ext}" if ep.ext else ".m4a"
    target_name = pick_episode_filename(ss, ep, ext)
    outtmpl = os.path.join(tmp_dir, "%(title)s.%(ext)s")  # let yt-dlp name temp artifacts

    ydl = make_ydl_downloader(outtmpl)

    append_album_log(album_dir, f"START download: {ep.title} ({ep.id})")
    try:
        info = ydl.extract_info(ep.url, download=True)
        # media path (yt-dlp may choose real name; find the file by info)
        # prefer actual downloaded file reported
        dname = info.get('_filename')
        if not dname or not os.path.isfile(dname):
            # fallback: search tmp_dir for largest fresh file
            cands = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir)
                     if os.path.isfile(os.path.join(tmp_dir, f)) and os.path.splitext(f)[1].lower() in ('.mp3','.m4a','.m4b','.flac','.ogg','.opus','.wav','.aac')]
            dname = max(cands, key=lambda p: os.path.getmtime(p)) if cands else None
        if not dname:
            append_album_log(album_dir, f"ERROR: no media file detected after download for ep {ep.id}")
            return None

        # move media + its .info.json into album root (final name)
        final_media = os.path.join(album_dir, target_name)
        ijson_src = dname + ".info.json"
        if os.path.isfile(final_media):
            # avoid duplicates: add (n) suffix
            base, ex = os.path.splitext(final_media)
            k=1
            while os.path.exists(final_media):
                final_media = f"{base} ({k}){ex}"
                k+=1

        shutil.move(dname, final_media)
        info_dst = os.path.join(nfo_dir, f"{os.path.splitext(target_name)[0]}.info.json")
        if os.path.isfile(ijson_src):
            shutil.move(ijson_src, info_dst)
        else:
            # write info we have
            with open(info_dst, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

        size = os.path.getsize(final_media)
        update_db_episode(db, ss, ep, final_media, info_dst, size)
        append_album_log(album_dir, f"DONE  download: {ep.title} → {os.path.basename(final_media)} ({size} bytes)")
        return album_dir
    except KeyboardInterrupt:
        append_album_log(album_dir, "INTERRUPTED by user.")
        raise
    except Exception as e:
        append_album_log(album_dir, f"ERROR download: {ep.title} → {e}")
        return None
    finally:
        # clean tmp
        try:
            if os.path.isdir(tmp_dir) and not os.listdir(tmp_dir):
                os.rmdir(tmp_dir)
        except: pass

# --------------------------
# tag_fixer integration
# --------------------------
def run_tag_fixer(album_dir: str, apply: bool, rename: bool, renumber: bool):
    # call tag_fixer.py in non-interactive mode
    fixer = os.path.join(BASE_DIR, "tag_fixer.py")
    if not os.path.isfile(fixer):
        log(f"{C.Y}[tag-fix] tag_fixer.py not found, skipping for {album_dir}{C.X}")
        return
    cmd = ["python3", fixer, album_dir, "--non-interactive"]
    if apply:
        cmd.append("--apply")
        if rename: cmd.append("--rename")
        if renumber: cmd.append("--renumber")
    log(f"{C.C}[tag-fix]{C.X} {album_dir} {'(apply)' if apply else '(preview)'}")
    try:
        os.spawnvp(os.P_WAIT, cmd[0], cmd)
    except Exception as e:
        log(f"{C.R}[tag-fix error]{C.X} {e}")

# --------------------------
# Interactive selection
# --------------------------
def ask_select_series(candidates: List[SeriesSnapshot]) -> List[SeriesSnapshot]:
    print(f"{C.BD}Discovered {len(candidates)} series. Choose what to download:{C.X}")
    for i, ss in enumerate(sorted(candidates, key=lambda s: s.title.lower()), start=1):
        print(f"  {i:>3}. {ss.title}  [{len(ss.episodes)} eps]")
    print("Enter numbers separated by spaces, 'a' for all, or empty to cancel.")
    choice = input("> ").strip().lower()
    if not choice:
        return []
    if choice == 'a':
        return candidates
    idxs = set()
    for tok in choice.split():
        if tok.isdigit():
            i = int(tok)
            if 1 <= i <= len(candidates):
                idxs.add(i)
    sorted_cands = sorted(candidates, key=lambda s: s.title.lower())
    return [sorted_cands[i-1] for i in sorted(idxs)]

# --------------------------
# Main
# --------------------------
def main():
    ap = argparse.ArgumentParser(description="MujRozhlas Audioloader (interactive by default).")
    ap.add_argument("--download", action="store_true", help="After report, perform downloads.")
    ap.add_argument("--unattended", action="store_true", help="No prompts; choose everything new.")
    ap.add_argument("--sequential", action="store_true", help="Force single-threaded downloads (default is parallel).")
    ap.add_argument("--max-workers", type=int, default=4, help="Parallel workers (default: 4).")
    ap.add_argument("--url", action="append", default=[], help="Priority URL(s) to process first.")
    # tag fixer
    ap.add_argument("--tag-fix", action="store_true", help="Run tag_fixer.py on albums touched in this run.")
    ap.add_argument("--tf-apply", action="store_true", help="With --tag-fix, apply changes.")
    ap.add_argument("--tf-rename", action="store_true", help="With --tag-fix/--tf-apply, also rename files.")
    ap.add_argument("--tf-renumber", action="store_true", help="With --tag-fix/--tf-apply, also renumber tracks.")
    ap.add_argument("--version", action="store_true", help="Show version and exit")
    args = ap.parse_args()
    # after parsing:
    if args.version:
        print(f"<command-name> {__version__}")  # e.g., "tag-fixer" or "audioloader"
        return 0  # or sys.exit(0)

    db = load_db()

    # load channels
    channels: List[str] = []
    if os.path.exists(SERIES_FILE):
        try:
            with open(SERIES_FILE, 'r', encoding='utf-8') as f:
                js = json.load(f)
            if isinstance(js, list):
                channels = js
        except Exception as e:
            log(f"{C.R}Cannot read {SERIES_FILE}: {e}{C.X}")

    # priority URLs first (dedup)
    all_entry_points = list(dict.fromkeys(args.url + channels))

    if not all_entry_points:
        log(f"{C.Y}No input URLs. Put channel URLs into {SERIES_FILE} or pass --url ...{C.X}")
        return 2

    # discover
    global_series: Dict[str, SeriesSnapshot] = {}
    for ch in all_entry_points:
        try:
            log(f"{C.B}Discovering:{C.X} {ch}")
            s_map = gather_series_from_channel(ch)
            merge_series_maps(global_series, s_map)
        except Exception as e:
            log(f"{C.R}Discovery error for {ch}: {e}{C.X}")

    # classify & report
    rows: List[Tuple[SeriesSnapshot, SeriesStatus]] = []
    for ss in global_series.values():
        rows.append((ss, classify_series(ss, db)))
    print_status_table(rows)

    if not args.download:
        log(f"{C.G}Report complete. (Use --download to fetch media){C.X}")
        return 0

    # choose what to download
    series_to_fetch: List[SeriesSnapshot] = []
    if args.unattended:
        # everything that isn't already complete
        for ss, st in rows:
            if st.status in ("New", "Progress", "Truncated"):
                series_to_fetch.append(ss)
        log(f"{C.Y}Unattended:{C.X} selected {len(series_to_fetch)} series to download.")
    else:
        series_to_fetch = ask_select_series([ss for ss,_ in rows])
        if not series_to_fetch:
            log(f"{C.Y}Nothing selected. Exiting.{C.X}")
            return 0

    # prepare download list (episodes not present)
    todo: List[Tuple[SeriesSnapshot, Episode]] = []
    for ss in series_to_fetch:
        for ep in ss.episodes.values():
            if not already_have(db, ss, ep):
                todo.append((ss, ep))

    if not todo:
        log(f"{C.G}No new episodes to download.{C.X}")
        return 0

    log(f"{C.BD}Planned downloads:{C.X} {len(todo)} episode(s) across {len(series_to_fetch)} series.")
    touched_albums = set()

    def _task(item):
        ss, ep = item
        res = download_episode(ss, ep, db)
        if res:
            touched_albums.add(res)

    try:
        if args.sequential or args.max_workers <= 1:
            for item in todo:
                _task(item)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
                list(ex.map(_task, todo))
    except KeyboardInterrupt:
        log(f"{C.R}Interrupted by user. Partial progress saved.{C.X}")

    save_db(db)

    # tag fixing if requested
    if args.tag_fix and touched_albums:
        for album in sorted(touched_albums):
            run_tag_fixer(album, apply=args.tf_apply, rename=args.tf_rename, renumber=args.tf_renumber)

    log(f"{C.G}All done.{C.X}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
