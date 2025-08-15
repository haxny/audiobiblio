#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tags_analyze_folder.py

ONE-IN-ALL TOOL for audiobook tag analysis and correction.

Workflow
--------
1) Deep scan folder with ExifTool → build suggestions
2) Show neat, color-coded review (album first, then only track diffs)
3) Let you confirm/modify suggestions interactively
4) Ask once: "Apply these changes?" → if yes, write with Mutagen
5) Save:
   - _tags_suggestions.json (with original & final)
   - _tags_report.csv
   - album.nfo (album-level doc, legacy notes, raw tag dump)

Key Features
------------
- UTF-8 everywhere; detect mojibake (broken encoding) before diacritics strip
- Optional diacritics removal in tags (kept ON by default, configurable)
- ID3v1 vs ID3v2 merge suggestions; prefer v2 but offer promote v1
- Album-level detection from folder names (Author, (Year), Title)
- Series inference from parent folder child numbering (01, 02, …)
- Performer detection from comments ("cte/čte/read by …")
- Translator detection from filenames/comments ("prekl/překlad/translated by …")
- Genre suggestion (Audiokniha / Audiokniha (SK) / Audiobook)
- Track 00 intro handling (skip in renumbering if enabled)
- Optional renumbering and file renaming from tags
- Cover art unification across all files in album
- Rich album.nfo with [AUTO]/[USER], legacy text ingest, raw tag dump
- Change safety: nothing is modified until final per-folder confirmation

Notes
-----
- This revision intentionally avoids online lookups (databazeknih.cz, etc.).
  We'll add those in the next step as discussed.
"""

import os
import re
import sys
import csv
import json
import hashlib
import shutil
import argparse
import subprocess
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# =========================
# Config (tweak as you like)
# =========================

CONFIG = {
    "strip_diacritics_in_tags": True,     # final tag values without diacritics
    "prefer_id3v2": True,                 # when merging, prefer v2 unless user overrides
    "skip_intro_00_when_renumber": True,  # skip files like "00 Uvod/Intro" when renumbering
    "default_genre": "Audiokniha",        # cs default; override interactively
    "supported_audio_exts": (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac"),
    "cover_preferred_names": ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"),
    "script_version": "2.0"
}

# =========================
# Console colors
# =========================

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GRAY = "\033[90m"
    GREEN = "\033[92m"
    MAGENTA = "\033[95m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"

def cgray(s): return f"{C.GRAY}{s}{C.RESET}"
def cdim(s): return f"{C.DIM}{s}{C.RESET}"
def cgreen(s): return f"{C.GREEN}{s}{C.RESET}"
def cmag(s): return f"{C.MAGENTA}{s}{C.RESET}"
def cyellow(s): return f"{C.YELLOW}{s}{C.RESET}"
def cred(s): return f"{C.RED}{s}{C.RESET}"
def ccyan(s): return f"{C.CYAN}{s}{C.RESET}"

# =========================
# Helpers
# =========================

def strip_diacritics(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def maybe_strip_diacritics(s: str) -> str:
    return strip_diacritics(s) if CONFIG["strip_diacritics_in_tags"] else s

def looks_mojibake(s: str) -> bool:
    # Very simple heuristic for Central European mojibake
    bad_fragments = ["È", "Ø", "ø", "Å", "Ã", "œ", "ž", "Å¡", "Ã¡", "Ã©", "Ã¨", "Ãº", "Ã±", "Ã¾", "Â"]
    return any(bad in s for bad in bad_fragments)

def fix_encoding_from_filename(tag_value: str, filename: str) -> Optional[str]:
    """
    Heuristic: if title/comment in tags shows mojibake but filename doesn't,
    attempt to derive a sane suggestion from filename (basename without extension).
    """
    if not tag_value:
        return None
    if not looks_mojibake(tag_value):
        return None
    base = os.path.splitext(os.path.basename(filename))[0]
    # Drop common track-number prefixes from filename, leave readable words
    base = re.sub(r"^\s*\d+\s*[-_.]?\s*", "", base)
    base = base.strip()
    if base and not looks_mojibake(base):
        # prefer filename (no diacritics in tags later if configured)
        return maybe_strip_diacritics(base)
    # fallback: strip diacritics from original
    return maybe_strip_diacritics(tag_value)

def run_exiftool(folder: str) -> List[Dict[str, Any]]:
    cmd = ["exiftool", "-j", "-charset", "utf8", "-api", "largefilesupport=1", folder]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        print(cred("ExifTool error:"))
        print(res.stderr.decode("utf-8", errors="ignore"))
        return []
    try:
        data = json.loads(res.stdout.decode("utf-8"))
        return [e for e in data if any(str(e.get("SourceFile","")).lower().endswith(ext)
                                       for ext in CONFIG["supported_audio_exts"])]
    except json.JSONDecodeError:
        print(cred("Error: Could not parse ExifTool JSON output."))
        return []

def load_existing_json(folder: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(folder, "_tags_suggestions.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def save_json(folder: str, data: Dict[str, Any]) -> None:
    path = os.path.join(folder, "_tags_suggestions.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def save_csv(folder: str, rows: List[List[str]]) -> None:
    path = os.path.join(folder, "_tags_report.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["File", "Tag", "Original", "Suggested", "Decision", "SourceNote"])
        for r in rows:
            w.writerow(r)

def parse_folder_metadata(folder: str) -> Dict[str, Optional[str]]:
    """
    Accepts:
      "Author - (YYYY) Title"
      "Author - Title"
    """
    folder_name = os.path.basename(folder)
    author, year, title = None, None, None
    parts = [p.strip() for p in folder_name.split(" - ")]
    if len(parts) == 3:
        author, year_part, title = parts
        m = re.search(r"\((\d{4})\)", year_part)
        if m: year = m.group(1)
    elif len(parts) == 2:
        author, title = parts
    # normalize (strip diacritics later)
    return {
        "author": author,
        "year": year,
        "title": title
    }

def detect_series_from_parent(folder: str) -> Tuple[Optional[str], Optional[int]]:
    """
    If parent folder looks like a series container and child folder names start with 01, 02, ...
    infer series name and index of current folder.
    """
    parent = os.path.dirname(folder)
    me = os.path.basename(folder)
    if not parent or parent == folder:
        return None, None
    siblings = [d for d in os.listdir(parent) if os.path.isdir(os.path.join(parent, d))]
    # series name might be parent (strip author)
    series = os.path.basename(parent)
    # try to find "NN " at start of folder name
    m = re.match(r"^\s*(\d{1,3})\b", me)
    idx = int(m.group(1)) if m else None
    return series, idx

def clean_filename(s: str) -> str:
    s = re.sub(r'[\\/*?:"<>|]', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def collect_legacy_text(folder: str) -> List[Tuple[str, str, str]]:
    legacy = []
    for fname in os.listdir(folder):
        low = fname.lower()
        if low.endswith((".txt", ".nfo")) and not fname.startswith("_tags"):
            try:
                fpath = os.path.join(folder, fname)
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read().strip()
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
                legacy.append((fname, mtime, content))
            except:
                pass
    return legacy

def hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

# =========================
# Suggestions builder
# =========================

MAIN_TAGS = ["Album", "Artist", "AlbumArtist", "Performer", "Genre", "Date", "Comment", "Title", "Track", "Translator", "DiscNumber"]

def suggest_album_level(folder: str, exif: List[Dict[str,Any]]) -> Dict[str, Any]:
    fld = parse_folder_metadata(folder)
    series, series_idx = detect_series_from_parent(folder)

    # Pull hints from files
    artists = set()
    albums  = set()
    comments = []
    for e in exif:
      if "Artist" in e: artists.add(str(e["Artist"]))
      if "Album" in e: albums.add(str(e["Album"]))
      if "Comment" in e: comments.append(str(e["Comment"]))

    # Initial guesses
    author_guess = fld["author"]
    title_guess  = fld["title"]
    year_guess   = fld["year"]

    # If existing tags are all-caps, prefer nicely-cased variant
    def tidy_case(x: Optional[str]) -> Optional[str]:
        if not x: return None
        if x.isupper():
            return x.title()
        return x

    # Performer from "Čte:" / "Cte:" in comments
    perf_guess = None
    for c in comments:
        m = re.search(r"(?:čte|cte)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            perf_guess = m.group(1).strip()
            break

    # Translator (very rough local heuristics)
    translator_guess = None
    for c in comments:
        m = re.search(r"(?:překlad|preklad|translated\s*by)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            translator_guess = m.group(1).strip()
            break

    # Genre guess (local; will confirm later)
    genre_guess = CONFIG["default_genre"]

    # Build album name: Series - ## BookTitle
    album_guess = None
    if series and series_idx:
        # If folder title equals series name, keep only series - ## (no repeated title)
        bt = title_guess or ""
        if series.strip().lower() == (title_guess or "").strip().lower():
            album_guess = f"{series} - {series_idx:02d}"
        else:
            album_guess = f"{series} - {series_idx:02d} {bt}"
    else:
        album_guess = title_guess or (list(albums)[0] if albums else None)

    # Normalize diacritics (final output)
    def norm(x: Optional[str]) -> Optional[str]:
        return maybe_strip_diacritics(x) if x else x

    return {
        "auto": {
            "Artist": norm(tidy_case(author_guess)),
            "AlbumArtist": norm(tidy_case(author_guess)),
            "Performer": norm(perf_guess) if perf_guess else "",
            "Translator": norm(translator_guess) if translator_guess else "",
            "Genre": norm(genre_guess) if genre_guess else "",
            "Date": year_guess or "",         # keep numeric year here; we can later map to TDRC
            "Album": norm(album_guess) if album_guess else ""
        },
        "notes": {
            "why": "Album derived from folder/series; performer/translator heuristics from comments; diacritics stripped per config."
        }
    }

def suggest_track_level(exif: List[Dict[str,Any]]) -> Dict[str, Dict[str,Dict[str,str]]]:
    """
    For each file, suggest fixes:
    - Encoding fixes (mojibake)
    - Title cleanup (remove leading numbers if Track present)
    - ID3v1 vs v2 merge suggestion when they differ
    """
    out = {}
    for e in exif:
        sf = e.get("SourceFile")
        track_sugs: Dict[str, Dict[str, str]] = {}

        # Encoding fixes (Title/Comment/Performer)
        for tag in ("Title", "Comment", "Performer"):
            orig = str(e.get(tag, "")) if e.get(tag) is not None else ""
            if not orig:
                continue
            enc_fix = fix_encoding_from_filename(orig, sf)
            if enc_fix and enc_fix != orig:
                track_sugs[tag] = {"original": orig, "suggested": enc_fix, "source": "ENC FIX from filename"}

        # Title numbering removal if Track present
        title = str(e.get("Title", "")) if e.get("Title") is not None else ""
        track = str(e.get("Track", "")) if e.get("Track") is not None else ""
        if title and track:
            m = re.match(r"^\s*\d{1,3}\s*[-_.]?\s*(.*)$", title)
            if m:
                sug = maybe_strip_diacritics(m.group(1).strip())
                if sug and sug != title:
                    track_sugs["Title"] = {"original": title, "suggested": sug, "source": "Title: drop leading number (track set)"}

        # ID3v1 vs ID3v2 merge (only if both present and differ)
        for pair in (("ID3v1:Title", "ID3v2:Title"),
                     ("ID3v1:Artist", "ID3v2:Artist"),
                     ("ID3v1:Album",  "ID3v2:Album")):
            v1 = e.get(pair[0]); v2 = e.get(pair[1])
            if v1 and v2 and str(v1) != str(v2):
                prefer = "ID3v2→keep" if CONFIG["prefer_id3v2"] else "Promote ID3v1"
                track_sugs[pair[1]] = {
                    "original": str(v2),
                    "suggested": maybe_strip_diacritics(str(v1)) if not CONFIG["prefer_id3v2"] else str(v2),
                    "source": f"ID3v1 vs v2 differ ({prefer}); you may merge into v2"
                }

        if track_sugs:
            out[sf] = track_sugs
    return out

# =========================
# NFO builder
# =========================

def build_nfo(folder: str, suggestions: Dict[str,Any], exif_data: List[Dict[str,Any]], legacy_notes: List[Tuple[str,str,str]]) -> None:
    nfo_path = os.path.join(folder, "album.nfo")
    now = datetime.now().isoformat(timespec="seconds")
    lines: List[str] = []
    lines.append("ALBUM INFO PACKAGE")
    lines.append(f"Scan Date: {now}")
    lines.append(f"Script Version: {CONFIG['script_version']}")
    lines.append("")

    # Album metadata
    lines.append("[ALBUM METADATA - AUTO]")
    for k, v in suggestions["_album"]["auto"].items():
        lines.append(f"{k}: [AUTO] {v}")
    if suggestions["_album"].get("user"):
        lines.append("")
        lines.append("[ALBUM METADATA - USER]")
        for k, v in suggestions["_album"]["user"].items():
            lines.append(f"{k}: [USER] {v}")

    # Cover info
    cover = suggestions.get("_cover", {})
    if cover:
        lines.append("")
        lines.append("[COVER ART]")
        for k, v in cover.items():
            lines.append(f"{k}: {v}")

    # Legacy text
    if legacy_notes:
        lines.append("")
        lines.append("[LEGACY INFORMATION]")
        for fname, mtime, content in legacy_notes:
            lines.append(f"Source: {fname} (modified: {mtime})")
            lines.append(content)
            lines.append("---")

    # Raw dump
    lines.append("")
    lines.append("[RAW TAG DUMP]")
    for entry in exif_data:
        lines.append(json.dumps(entry, indent=2, ensure_ascii=False))
        lines.append("---")

    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# =========================
# Mutagen write helpers
# =========================

def mutagen_write(file_path: str, tags: Dict[str, Any], embed_image: Optional[bytes]) -> Tuple[bool, str]:
    """
    Write tags using Mutagen. Basic, format-aware writing for MP3/FLAC/M4A/OGG/OPUS.
    Returns (ok, message).
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, TCON, TDRC, COMM, TRCK, TPE3, TPOS, APIC, ID3NoHeaderError
            try:
                id3 = ID3(file_path)
            except ID3NoHeaderError:
                id3 = ID3()
            def set_text(frame_cls, key, val):
                if val not in (None, ""):
                    id3.setall(key, [])
                    id3.add(frame_cls(encoding=3, text=str(val)))
            set_text(TIT2, "TIT2", tags.get("Title"))
            set_text(TALB, "TALB", tags.get("Album"))
            set_text(TPE1, "TPE1", tags.get("Artist"))
            set_text(TPE2, "TPE2", tags.get("AlbumArtist"))
            set_text(TCON, "TCON", tags.get("Genre"))
            set_text(TDRC, "TDRC", tags.get("Date"))
            set_text(TPE3, "TPE3", tags.get("Performer"))  # Conductor/Performer field
            if tags.get("DiscNumber"):
                set_text(TPOS, "TPOS", str(tags["DiscNumber"]))
            if tags.get("Translator"):
                # No native "translator"; store via TXXX:TRANSLATOR
                from mutagen.id3 import TXXX
                id3.setall("TXXX", [TXXX(encoding=3, desc="TRANSLATOR", text=str(tags["Translator"]))])
            if tags.get("Comment"):
                id3.setall("COMM", [])
                id3.add(COMM(encoding=3, lang="eng", desc="", text=str(tags["Comment"])))
            if tags.get("Track"):
                set_text(TRCK, "TRCK", str(tags["Track"]))
            # Cover
            if embed_image:
                id3.delall("APIC")
                id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=embed_image))
            id3.save(file_path, v2_version=3)
            return True, "OK"

        elif ext in (".flac",):
            import mutagen.flac as MF
            f = MF.FLAC(file_path)
            def setv(k,v):
                if v not in (None, ""):
                    f[k] = str(v)
                elif k in f: del f[k]
            setv("title", tags.get("Title"))
            setv("album", tags.get("Album"))
            setv("artist", tags.get("Artist"))
            setv("albumartist", tags.get("AlbumArtist"))
            setv("genre", tags.get("Genre"))
            setv("date", tags.get("Date"))
            setv("performer", tags.get("Performer"))
            setv("discnumber", tags.get("DiscNumber"))
            setv("comment", tags.get("Comment"))
            if tags.get("Translator"):
                f["translator"] = str(tags["Translator"])
            # Picture
            if embed_image:
                pic = MF.Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.data = embed_image
                f.clear_pictures()
                f.add_picture(pic)
            f.save()
            return True, "OK"

        else:
            # Basic fallback for formats (m4a, ogg, opus, wav, aac) — minimal fields
            from mutagen import File
            f = File(file_path, easy=True)
            if f is None:
                return False, "Unsupported file for writing"
            def setv(k,v):
                if v not in (None, ""):
                    f[k] = [str(v)]
                elif k in f: del f[k]
            # Map common Easy fields
            setv("title", tags.get("Title"))
            setv("album", tags.get("Album"))
            setv("artist", tags.get("Artist"))
            setv("albumartist", tags.get("AlbumArtist"))
            setv("genre", tags.get("Genre"))
            setv("date", tags.get("Date"))
            # performer/translator not standardized in easy tags; skip
            f.save()
            # (Cover embedding for m4a/ogg/opus needs format-specific handling; omitted here)
            return True, "OK"

    except Exception as e:
        return False, f"Write error: {e}"

# =========================
# Interactive review
# =========================

def ask_choice(prompt: str, choices: str = "a/k/m/s", default: str = "a") -> str:
    print(f"{prompt} [{choices}] (default {default})")
    ans = input("> ").strip().lower()
    if not ans: ans = default
    return ans

def review_album_level(album_auto: Dict[str,str], existing_user: Dict[str,str]) -> Dict[str,str]:
    print(f"\n{C.BOLD}=== ALBUM-LEVEL TAGS (inferred){C.RESET}")
    out = dict(existing_user) if existing_user else {}
    for i, key in enumerate(["Album", "Artist", "AlbumArtist", "Performer", "Translator", "Genre", "Date"], start=1):
        sug = album_auto.get(key, "")
        orig = out.get(key, "")
        code = f"a{i:02d}"
        print(cmag(f"{code}".ljust(6)), end="")
        print(f"{key:<14} {cgray('original:')} {orig!s:<30}  {cgray('suggested:')} {sug!s}")
        ch = ask_choice("  choose: (a)ccept, (k)eep, (m)anual-from-original, (s)tart-from-suggestion", "a/k/m/s", "a")
        if ch == "a":
            out[key] = sug
        elif ch == "k":
            out[key] = orig or sug
        elif ch == "m":
            out[key] = input(f"  enter new value (start original='{orig}'): ") or orig
        elif ch == "s":
            out[key] = input(f"  enter new value (start suggestion='{sug}'): ") or sug
    return out

def review_tracks(track_sugs: Dict[str,Dict[str,Dict[str,str]]]) -> Dict[str,Dict[str,str]]:
    print(f"\n{C.BOLD}=== TRACK-LEVEL DIFFERENCES ONLY{C.RESET}")
    final = {}
    for idx, (sf, tags) in enumerate(sorted(track_sugs.items()), start=1):
        print(ccyan(f"--- {os.path.basename(sf)} --- ({idx})"))
        final[sf] = {}
        for t_idx, (tag, info) in enumerate(tags.items(), start=1):
            code = f"t{idx:02d}.{t_idx:02d}"
            orig = info["original"]; sug = info["suggested"]; src = info["source"]
            print(cmag(code.ljust(8)), f"{tag:<18} {orig!s:<40} → {cyellow(sug!s)} {cgray(f'[{src}]')}")
            ch = ask_choice("  choose: (a)ccept, (k)eep, (m)anual-from-original, (s)tart-from-suggestion", "a/k/m/s", "a")
            if ch == "a":
                final[sf][tag] = sug
            elif ch == "k":
                final[sf][tag] = orig
            elif ch == "m":
                final[sf][tag] = input(f"  new value (orig='{orig}'): ") or orig
            elif ch == "s":
                final[sf][tag] = input(f"  new value (sug='{sug}'): ") or sug
    return final

# =========================
# Cover art unify (local)
# =========================

def find_cover_file(folder: str) -> Optional[str]:
    # prefer typical names first
    for name in CONFIG["cover_preferred_names"]:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    # fallback: any image
    for f in os.listdir(folder):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            return os.path.join(folder, f)
    return None

def read_binary(p: str) -> Optional[bytes]:
    try:
        with open(p, "rb") as f:
            return f.read()
    except:
        return None

# =========================
# Build apply plan
# =========================

def build_apply_plan(folder: str,
                     exif: List[Dict[str,Any]],
                     album_final: Dict[str,str],
                     track_final: Dict[str,Dict[str,str]],
                     renumber: bool,
                     skip_intro_00: bool,
                     rename_files: bool) -> Tuple[List[Dict[str,Any]], List[List[str]]]:
    """
    Create one dict per file: {file, tags{..}, new_filename?, note}
    Also produce CSV rows.
    """
    plan = []
    csv_rows: List[List[str]] = []

    # numbering state
    next_track = 1

    # cover file (optional embed)
    cover_file = find_cover_file(folder)
    cover_bytes = read_binary(cover_file) if cover_file else None

    # discnumber from album if series index inferred inside album name " - NN "
    discnumber = None
    m = re.search(r" - (\d{2,3})\b", album_final.get("Album","") or "")
    if m:
        discnumber = int(m.group(1))

    for e in exif:
        sf = e["SourceFile"]
        base = os.path.basename(sf)
        ext = os.path.splitext(base)[1].lower()

        # Start with album-level tags (Artist/AlbumArtist/Album/Genre/Performer/Translator/Date)
        tags = {
            "Artist": album_final.get("Artist"),
            "AlbumArtist": album_final.get("AlbumArtist"),
            "Album": album_final.get("Album"),
            "Genre": album_final.get("Genre"),
            "Performer": album_final.get("Performer"),
            "Translator": album_final.get("Translator"),
            "Date": album_final.get("Date"),
            "DiscNumber": discnumber
        }

        # Take Title/Comment/Track from per-track review if present, else current file values (normalized)
        cur_title = str(e.get("Title", os.path.splitext(base)[0]))
        cur_comment = str(e.get("Comment", ""))
        cur_track = str(e.get("Track", ""))

        if sf in track_final:
            for k, v in track_final[sf].items():
                if k.lower().startswith("id3v2:"):
                    # merged into v2 — map by suffix
                    k2 = k.split(":", 1)[1]
                    if k2.lower() == "title": tags["Title"] = v
                    elif k2.lower() == "artist": tags["Artist"] = v
                    elif k2.lower() == "album": tags["Album"] = v
                elif k.lower() == "title": tags["Title"] = v
                elif k.lower() == "comment": tags["Comment"] = v
                elif k.lower() == "performer": tags["Performer"] = v
        # defaults if not changed
        tags.setdefault("Title", maybe_strip_diacritics(cur_title))
        tags.setdefault("Comment", maybe_strip_diacritics(cur_comment))

        # Track numbering
        if renumber:
            is_intro = False
            lb = base.lower()
            if lb.startswith("00") or ("uvod" in lb) or ("intro" in lb):
                is_intro = True
            if skip_intro_00 and is_intro:
                tags["Track"] = cur_track or ""  # leave as-is
            else:
                tags["Track"] = f"{next_track:02d}"
                next_track += 1
        else:
            tags["Track"] = cur_track

        # File renaming (optional)
        new_filename = None
        if rename_files:
            tnum = tags.get("Track") or ""
            tnum = str(tnum).split("/")[0] if tnum else ""
            tnum = (tnum.zfill(2) if tnum.isdigit() and len(tnum) < 2 else tnum)
            ttitle = clean_filename(tags.get("Title") or os.path.splitext(base)[0])
            new_filename = f"{(tnum + ' ') if tnum else ''}{ttitle}{ext}"
            if new_filename != base:
                csv_rows.append([sf, "FileName", base, new_filename, "RENAME", "from tags"])
        # CSV tag rows (diffs)
        for k in ("Title","Album","Artist","AlbumArtist","Performer","Translator","Genre","Date","DiscNumber","Track","Comment"):
            orig = str(e.get(k, "")) if e.get(k) is not None else ""
            sug  = tags.get(k)
            if sug is None: continue
            if str(sug) != orig:
                csv_rows.append([sf, k, orig, str(sug), "TAG", "apply plan"])

        plan.append({
            "file": sf,
            "tags": tags,
            "new_filename": new_filename,
            "embed_cover": cover_bytes
        })

    return plan, csv_rows

# =========================
# Main
# =========================

def build_suggestions(folder: str, force: bool) -> Tuple[Dict[str,Any], List[List[str]], List[Dict[str,Any]], List[Tuple[str,str,str]]]:
    print(f"{C.BOLD}Scanning folder:{C.RESET} {folder}")
    exif_data = run_exiftool(folder)
    if not exif_data:
        print(cred("No supported audio metadata found."))
        sys.exit(2)

    existing = None if force else load_existing_json(folder)

    # Album-level
    album = suggest_album_level(folder, exif_data)
    album_user = existing["_album"]["user"] if existing and "_album" in existing and "user" in existing["_album"] else {}

    # Track-level diffs
    track_diffs = suggest_track_level(exif_data)

    # Suggestions struct
    suggestions = {
        "_scan_metadata": {
            "scan_date": datetime.now().isoformat(timespec="seconds"),
            "script_version": CONFIG["script_version"]
        },
        "_album": {
            "auto": album["auto"],
            "user": album_user
        },
        "_cover": {},  # will be filled later
        "tracks": track_diffs
    }

    # CSV rows (start empty — we fill after review/apply plan)
    csv_rows: List[List[str]] = []
    legacy = collect_legacy_text(folder)

    return suggestions, csv_rows, exif_data, legacy

def build_and_write_nfo(folder: str, suggestions: Dict[str,Any], exif_data: List[Dict[str,Any]], legacy: List[Tuple[str,str,str]]) -> None:
    build_nfo(folder, suggestions, exif_data, legacy)

def interactive_flow(folder: str,
                     suggestions: Dict[str,Any],
                     exif_data: List[Dict[str,Any]],
                     renumber: bool,
                     skip_intro: bool,
                     rename_files: bool) -> None:

    # Album-level review
    album_final = review_album_level(suggestions["_album"]["auto"], suggestions["_album"].get("user", {}))
    suggestions["_album"]["user"] = album_final

    # Track-level review
    track_final = review_tracks(suggestions["tracks"])

    # Cover info populate (local only)
    cover_path = find_cover_file(folder)
    if cover_path:
        suggestions["_cover"] = {
            "CoverFile": os.path.basename(cover_path),
            "CoverPath": cover_path
        }

    # Build apply plan (preview)
    plan, csv_rows = build_apply_plan(folder, exif_data, album_final, track_final, renumber, skip_intro, rename_files)

    print(f"\n{C.BOLD}=== APPLY PREVIEW ==={C.RESET}")
    for item in plan:
        base = os.path.basename(item["file"])
        print(cyan := ccyan(f"{base}"))
        t = item["tags"]
        print(f"  → Title: {t.get('Title')}")
        print(f"  → Track: {t.get('Track')}")
        if item.get("new_filename") and item["new_filename"] != base:
            print(f"  → Rename: {base}  →  {cgreen(item['new_filename'])}")
    confirm = ask_choice(cyellow("\nApply all changes to files?"), "y/n", "n")
    # Save logs regardless
    save_json(folder, suggestions)
    save_csv(folder, csv_rows)
    build_and_write_nfo(folder, suggestions, exif_data, collect_legacy_text(folder))
    if confirm != "y":
        print(cgray("No changes applied. Suggestions, CSV, and NFO saved."))
        return

    # Apply with Mutagen
    failures = 0
    for item in plan:
        ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
        if not ok:
            print(cred(f"Write failed: {item['file']} → {msg}"))
            failures += 1
            continue
        # Rename after successful write
        nf = item.get("new_filename")
        if nf:
            old = item["file"]
            new = os.path.join(os.path.dirname(old), nf)
            if os.path.abspath(old) != os.path.abspath(new):
                try:
                    shutil.move(old, new)
                except Exception as e:
                    print(cred(f"Rename failed: {old} → {new}: {e}"))

    if failures:
        print(cred(f"Done with {failures} failures."))
    else:
        print(cgreen("All changes applied successfully."))

def main():
    ap = argparse.ArgumentParser(description="Analyze and (optionally) retag audiobook folders.")
    ap.add_argument("folder", help="Target folder")
    ap.add_argument("--force", action="store_true", help="Ignore previous _tags_suggestions.json")
    ap.add_argument("--renumber", action="store_true", help="Renumber tracks (skips 00/intro if configured)")
    ap.add_argument("--no-skip-intro", action="store_true", help="Do not skip '00/intro/uvod' when renumbering")
    ap.add_argument("--rename", action="store_true", help="Rename files from tags after writing")
    args = ap.parse_args()

    if not os.path.isdir(args.folder):
        print(cred("Folder not found"))
        sys.exit(2)

    # Respect toggle for intro skip
    if args.no_skip_intro:
        CONFIG["skip_intro_00_when_renumber"] = False

    suggestions, csv_rows, exif_data, legacy = build_suggestions(args.folder, force=args.force)
    # Always produce NFO now (has raw dump and legacy); we’ll update again after review/apply
    build_and_write_nfo(args.folder, suggestions, exif_data, legacy)

    interactive_flow(args.folder, suggestions, exif_data,
                     renumber=args.renumber,
                     skip_intro=CONFIG["skip_intro_00_when_renumber"],
                     rename_files=args.rename)

if __name__ == "__main__":
    main()
