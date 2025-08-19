#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tag_fixer.py

ONE-IN-ALL TOOL for audiobook tag analysis and correction.
- Importable API: fix_tags(folder, ...)
- CLI with interactive or non-interactive review
- Safe by default: no file writes unless --apply (or apply=True in API)

Changes vs tags_analyze_folder.py:
- RENAMED to tag_fixer.py, exposed fix_tags() for programmatic use
- --non-interactive + --apply to accept suggestions without prompts
- Album-level overrides via CLI or API (author/year/album/performer/translator/genre)
- Optional diacritics stripping toggle via CLI or API
- Return codes and cleaner error messages
"""

import os
import re
import sys
import csv
import json
import shutil
import argparse
import subprocess
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

def check_dependencies():
    # exiftool
    from shutil import which
    if which("exiftool") is None:
        print(cred("Missing dependency: exiftool (install with: brew install exiftool)"))
        sys.exit(2)
    # mutagen
    try:
        import mutagen  # noqa
    except Exception:
        print(cred("Missing dependency: mutagen (install in your venv: pip install mutagen)"))
        sys.exit(2)

# =========================
# Config (defaults)
# =========================

CONFIG = {
    "strip_diacritics_in_tags": True,     # final tag values without diacritics
    "prefer_id3v2": True,                 # when merging, prefer v2 unless user overrides
    "skip_intro_00_when_renumber": True,  # skip files like "00 Uvod/Intro" when renumbering
    "default_genre": "Audiokniha",
    "supported_audio_exts": (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac"),
    "cover_preferred_names": ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"),
    "script_version": "3.0",
    "strip_all_text_tags": False,          # force strip diacritics from every text tag at write time
    "bulk_replacements": [],               # default empty; can be extended via JSON or CLI
}

# ---- Bulk replace defaults (optional, safe to keep empty) ----
CONFIG["replace_rules_default"] = [
    # your Czech chapter mapping:
    {"pattern": ", část první",  "replacement": " 01", "regex": False, "fields": []},
    {"pattern": ", část druhá",  "replacement": " 02", "regex": False, "fields": []},
    {"pattern": ", část třetí",  "replacement": " 03", "regex": False, "fields": []},
    {"pattern": ", část čtvrtá", "replacement": " 04", "regex": False, "fields": []},
]

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

def _load_replace_file(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            out = []
            for r in data:
                if isinstance(r, dict) and "pattern" in r and "replacement" in r:
                    out.append({
                        "pattern": str(r["pattern"]),
                        "replacement": str(r["replacement"]),
                        "regex": bool(r.get("regex", False)),
                        "fields": list(r.get("fields", [])) if r.get("fields") else []
                    })
            return out
    except Exception:
        pass
    return []

_TEXT_TAG_KEYS = {"Title","Album","Artist","AlbumArtist","Performer","Translator","Genre","Date","Comment"}

def _apply_replacements_to_value(val: str, rules: list[dict], strip_all: bool) -> str:
    if val in (None, ""):
        return val
    s = str(val)
    if strip_all:
        s = strip_diacritics(s)
    for r in rules or []:
        pat = r.get("pattern","")
        rep = r.get("replacement","")
        if not pat:
            continue
        if r.get("regex"):
            try:
                s = re.sub(pat, rep, s)
            except re.error:
                continue
        else:
            s = s.replace(pat, rep)
    return s


def strip_diacritics(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def maybe_strip_diacritics(s: str) -> str:
    return strip_diacritics(s) if CONFIG["strip_diacritics_in_tags"] else s

def looks_mojibake(s: str) -> bool:
    bad_fragments = ["È", "Ø", "ø", "Å", "Ã", "œ", "Å¡", "Ã¡", "Ã©", "Ã¨", "Ãº", "Ã±", "Ã¾", "Â"]
    return any(bad in s for bad in bad_fragments)

def fix_encoding_from_filename(tag_value: str, filename: str) -> Optional[str]:
    if not tag_value:
        return None
    if not looks_mojibake(tag_value):
        return None
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"^\s*\d+\s*[-_.]?\s*", "", base).strip()
    if base and not looks_mojibake(base):
        return maybe_strip_diacritics(base)
    return maybe_strip_diacritics(tag_value)

def run_exiftool(folder: str) -> List[Dict[str, Any]]:
    cmd = ["exiftool", "-j", "-charset", "utf8", "-api", "largefilesupport=1", folder]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print(cred("ExifTool not found. Please install exiftool."), file=sys.stderr)
        return []
    if res.returncode != 0:
        print(cred("ExifTool error:"), file=sys.stderr)
        print(res.stderr.decode("utf-8", errors="ignore"), file=sys.stderr)
        return []
    try:
        data = json.loads(res.stdout.decode("utf-8"))
        return [e for e in data if any(str(e.get("SourceFile","")).lower().endswith(ext)
                                       for ext in CONFIG["supported_audio_exts"])]
    except json.JSONDecodeError:
        print(cred("Error: Could not parse ExifTool JSON output."), file=sys.stderr)
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
    Accepts patterns like:
      "Author - (YYYY) Title"
      "Author - Title"
    Fallback: if nothing matches, use the whole folder name as 'title'.
    """
    folder_name = os.path.basename(folder).strip()
    author, year, title = None, None, None

    # 1) Author - (YYYY) Title
    m = re.match(r"^(?P<author>.+?)\s*-\s*\((?P<year>\d{4})\)\s*(?P<title>.+)$", folder_name)
    if m:
        author = m.group("author").strip()
        year = m.group("year").strip()
        title = m.group("title").strip()
        return {"author": author, "year": year, "title": title}

    # 2) Author - Title
    m = re.match(r"^(?P<author>.+?)\s*-\s*(?P<title>.+)$", folder_name)
    if m:
        author = m.group("author").strip()
        title = m.group("title").strip()
        return {"author": author, "year": None, "title": title}

    # 3) Fallback: use whole folder name as title
    return {"author": None, "year": None, "title": folder_name}

def detect_series_from_parent(folder: str) -> Tuple[Optional[str], Optional[int]]:
    parent = os.path.dirname(folder)
    me = os.path.basename(folder)
    if not parent or parent == folder:
        return None, None
    m = re.match(r"^\s*(\d{1,3})\b", me)
    idx = int(m.group(1)) if m else None
    series = os.path.basename(parent)
    return series, idx

def clean_filename(s: str) -> str:
    s = re.sub(r'[\\/*?:"<>|]', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def infer_track_from_filename(base: str) -> Optional[str]:
    """
    Try to extract a track number from the filename.
    Examples it catches:
      '1. díl; ...', '2 dil ...', '101 ...', '01 - title', '01_title', '01title'
    Returns zero-padded 'NN' or 'NNN' depending on width; else None.
    """
    name = os.path.splitext(base)[0]
    # common "NN." or "NN -" or "NN " patterns
    m = re.match(r"^\s*(\d{1,3})\s*([.\-–_:; ]|$)", name)
    if m:
        num = m.group(1)
        # zero-pad to 2 if <= 99; keep 3 digits if >= 100 (for disc+episode like 301…)
        if len(num) == 1:
            return num.zfill(2)
        return num
    # Czech-ish "1. díl" variants already covered by the above, but keep a fallback:
    m = re.match(r"^\s*(\d{1,3})\s*(?:d[ií]l)\b", name, flags=re.IGNORECASE)
    if m:
        num = m.group(1)
        return num.zfill(2) if len(num) < 2 else num
    return None

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
            except Exception:
                # Ignore unreadable legacy files
                pass
    return legacy

def read_binary(p: str) -> Optional[bytes]:
    try:
        with open(p, "rb") as f:
            return f.read()
    except Exception:
        return None

def _load_replace_file(path: str) -> list[dict]:
    """Load rules from a JSON file: list of {pattern, replacement, regex?, fields?}."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            out = []
            for r in data:
                if not isinstance(r, dict):
                    continue
                pat = r.get("pattern"); rep = r.get("replacement")
                if not isinstance(pat, str) or not isinstance(rep, str):
                    continue
                out.append({
                    "pattern": pat,
                    "replacement": rep,
                    "regex": bool(r.get("regex", False)),
                    "fields": list(r.get("fields", [])),
                })
            return out
    except Exception:
        pass
    return []


def _apply_rules_to_text(value: str, field_name: str, rules: list[dict], force_strip_all: bool) -> str:
    """Apply diacritics removal (global) + find/replace rules to a single text value."""
    if not isinstance(value, str):
        return value

    # 1) optional global diacritics stripping
    if force_strip_all and value:
        value = strip_diacritics(value)

    # 2) rules
    if not rules:
        return value

    out = value
    for r in rules:
        fields = r.get("fields") or []
        if fields and field_name not in fields:
            continue
        pat = r.get("pattern", "")
        rep = r.get("replacement", "")
        if not pat:
            continue
        if r.get("regex", False):
            try:
                out = re.sub(pat, rep, out)
            except re.error:
                # bad regex → skip
                pass
        else:
            out = out.replace(pat, rep)
    return out

# =========================
# Suggestions builder
# =========================

def summarize_missing_and_suggested(album_auto: Dict[str, str],
                                    exif_data: List[Dict[str,Any]],
                                    folder: str) -> None:
    """Print a short preface: folder, missing tags, and our suggestions."""
    present = {k: False for k in ["Album", "Artist", "AlbumArtist", "Performer", "Translator", "Genre", "Date"]}
    any_titles = False
    any_tracks = False

    for e in exif_data:
        for k in present.keys():
            if e.get(k):
                present[k] = True
        if e.get("Title"):
            any_titles = True
        if e.get("Track"):
            any_tracks = True

    missing = [k for k, ok in present.items() if not ok]
    print(f"\n{C.BOLD}Folder{C.RESET} {os.path.basename(folder)}")
    if missing or (not any_titles) or (not any_tracks):
        what: List[str] = []
        if missing:
            what.append(", ".join(missing))
        if not any_tracks:
            what.append("Track number(s)")
        if not any_titles:
            what.append("Track title(s)")
        print(f"- {C.BOLD}missing tags{C.RESET}: {', '.join(what) if what else '(none)'}")
    else:
        print(f"- {C.BOLD}missing tags{C.RESET}: (none)")

    print(f"- {C.BOLD}suggesting edits{C.RESET}:")
    for k in ["Album","Artist","AlbumArtist","Performer","Translator","Genre","Date"]:
        sug = album_auto.get(k, "")
        sug_disp = sug if sug else "(none)"
        print(f"  · {k:<12} {sug_disp}")

def interactive_flow(folder: str,
                     suggestions: Dict[str,Any],
                     exif_data: List[Dict[str,Any]],
                     renumber: bool,
                     skip_intro: bool,
                     rename_files: bool,
                     replace_rules: Optional[List[Dict[str,Any]]] = None,
                     strip_all_text: bool = False) -> None:

    # Preface: missing tags + proposed suggestions
    summarize_missing_and_suggested(suggestions["_album"]["auto"], exif_data, folder)

    # Album-level review…
    album_final = review_album_level(suggestions["_album"]["auto"], suggestions["_album"].get("user", {}))
    suggestions["_album"]["user"] = album_final
    ...

def suggest_album_level(folder: str,
                        exif: List[Dict[str,Any]],
                        overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Build album-level suggestions with sensible fallbacks and optional user overrides.
    - Accepts overrides in either lower-case or Title-Case keys.
    - Tidy-cases author/album-artist guesses when source tags are ALLCAPS.
    - Applies diacritics policy via maybe_strip_diacritics().
    """
    overrides = overrides or {}
    fld = parse_folder_metadata(folder)
    series, series_idx = detect_series_from_parent(folder)

    # final normalization (diacritics policy)
    def norm(x: Optional[str]) -> Optional[str]:
        return maybe_strip_diacritics(x) if x else x

    # gentle case tidy: if ALLCAPS -> Title Case, else keep as-is
    def tidy_case(x: Optional[str]) -> Optional[str]:
        if not x:
            return x
        return x.title() if x.isupper() else x

    # Pull hints from files
    artists: set[str] = set()
    albums: set[str] = set()
    comments: list[str] = []
    for e in exif:
        a = e.get("Artist")
        if a:
            artists.add(str(a))
        al = e.get("Album")
        if al:
            albums.add(str(al))
        c = e.get("Comment")
        if c:
            comments.append(str(c))

    # Initial guesses (safe defaults from folder name)
    author_guess = fld.get("author")
    title_guess  = fld.get("title")
    year_guess   = fld.get("year")

    # If no author but tags have a dominant artist, take the first
    if not author_guess and artists:
        author_guess = list(artists)[0]

    # Performer from "Čte|Cte:" in comments
    perf_guess = None
    for c in comments:
        m = re.search(r"(?:čte|cte)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            perf_guess = m.group(1).strip()
            break

    # Translator from comments
    translator_guess = None
    for c in comments:
        m = re.search(r"(?:překlad|preklad|translated\s*by)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            translator_guess = m.group(1).strip()
            break

    # Genre default
    genre_guess = CONFIG["default_genre"]

    # Album name: "Series - ## Title" OR fallback to folder/existing Album tag
    if series and series_idx:
        bt = title_guess or ""
        if series.strip().lower() == (title_guess or "").strip().lower():
            album_guess = f"{series} - {series_idx:02d}"
        else:
            album_guess = f"{series} - {series_idx:02d} {bt}".strip()
    else:
        album_guess = title_guess or (next(iter(albums)) if albums else os.path.basename(folder))

    # Helper: prefer lower-case override keys, but accept Title-Case too
    def get_ovr(*keys: str) -> Optional[str]:
        for k in keys:
            if k in overrides and overrides[k]:
                return overrides[k]
        return None

    # Apply overrides (downloader/CLI may send either style)
    artist_final       = get_ovr("artist", "Artist") or tidy_case(author_guess)
    albumartist_final  = get_ovr("album_artist", "AlbumArtist") or tidy_case(author_guess)
    album_final        = get_ovr("album", "Album") or album_guess
    performer_final    = get_ovr("performer", "Performer") or perf_guess
    translator_final   = get_ovr("translator", "Translator") or translator_guess
    genre_final        = get_ovr("genre", "Genre") or genre_guess
    date_final         = get_ovr("date", "Date") or year_guess

    return {
        "auto": {
            "Artist":       norm(artist_final) if artist_final else "",
            "AlbumArtist":  norm(albumartist_final) if albumartist_final else "",
            "Performer":    norm(performer_final) if performer_final else "",
            "Translator":   norm(translator_final) if translator_final else "",
            "Genre":        norm(genre_final) if genre_final else "",
            "Date":         str(date_final) if date_final else "",
            "Album":        norm(album_final) if album_final else "",
        },
        "notes": {
            "why": "Album derived from folder/series & overrides; performer/translator from comments; diacritics stripped per config."
        }
    }

def suggest_track_level(exif: List[Dict[str,Any]]) -> Dict[str, Dict[str,Dict[str,str]]]:
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for e in exif:
        sf = e.get("SourceFile")
        track_sugs: Dict[str, Dict[str, str]] = {}

        for tag in ("Title", "Comment", "Performer"):
            orig = str(e.get(tag, "")) if e.get(tag) is not None else ""
            if not orig:
                continue
            enc_fix = fix_encoding_from_filename(orig, sf)
            if enc_fix and enc_fix != orig:
                track_sugs[tag] = {"original": orig, "suggested": enc_fix, "source": "ENC FIX from filename"}

        title = str(e.get("Title", "")) if e.get("Title") is not None else ""
        track = str(e.get("Track", "")) if e.get("Track") is not None else ""
        if title and track:
            m = re.match(r"^\s*\d{1,3}\s*[-_.]?\s*(.*)$", title)
            if m:
                sug = maybe_strip_diacritics(m.group(1).strip())
                if sug and sug != title:
                    track_sugs["Title"] = {"original": title, "suggested": sug, "source": "Title: drop leading number (track set)"}

        for pair in (("ID3v1:Title", "ID3v2:Title"),
                     ("ID3v1:Artist", "ID3v2:Artist"),
                     ("ID3v1:Album",  "ID3v2:Album")):
            v1 = e.get(pair[0])
            v2 = e.get(pair[1])
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
    lines.append("[ALBUM METADATA - AUTO]")
    for k, v in suggestions["_album"]["auto"].items():
        lines.append(f"{k}: [AUTO] {v}")
    if suggestions["_album"].get("user"):
        lines.append("")
        lines.append("[ALBUM METADATA - USER]")
        for k, v in suggestions["_album"]["user"].items():
            lines.append(f"{k}: [USER] {v}")
    cover = suggestions.get("_cover", {})
    if cover:
        lines.append("")
        lines.append("[COVER ART]")
        for k, v in cover.items():
            lines.append(f"{k}: {v}")
    if legacy_notes:
        lines.append("")
        lines.append("[LEGACY INFORMATION]")
        for fname, mtime, content in legacy_notes:
            lines.append(f"Source: {fname} (modified: {mtime})")
            lines.append(content)
            lines.append("---")
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
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, TCON, TDRC, COMM, TRCK, TPOS, APIC, ID3NoHeaderError, TXXX
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
            if tags.get("DiscNumber"):
                set_text(TPOS, "TPOS", str(tags["DiscNumber"]))
            if tags.get("Translator"):
                id3.setall("TXXX", [TXXX(encoding=3, desc="TRANSLATOR", text=str(tags["Translator"]))])
            if tags.get("Comment"):
                id3.setall("COMM", [])
                id3.add(COMM(encoding=3, lang="eng", desc="", text=str(tags["Comment"])))
            if tags.get("Track"):
                set_text(TRCK, "TRCK", str(tags["Track"]))
            if tags.get("Performer"):
                id3.add(TXXX(encoding=3, desc="PERFORMER", text=str(tags["Performer"])))      
            if embed_image:
                id3.delall("APIC")
                id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=embed_image))
            id3.save(file_path, v2_version=3)
            return True, "OK"

        elif ext in (".flac",):
            import mutagen.flac as MF
            f = MF.FLAC(file_path)
            def setv(k, v):
                if v not in (None, ""):
                    f[k] = str(v)
                elif k in f:
                    del f[k]
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
            from mutagen import File
            f = File(file_path, easy=True)
            if f is None:
                return False, "Unsupported file for writing"
            def setv(k, v):
                if v not in (None, ""):
                    f[k] = [str(v)]
                elif k in f:
                    del f[k]
            setv("title", tags.get("Title"))
            setv("album", tags.get("Album"))
            setv("artist", tags.get("Artist"))
            setv("albumartist", tags.get("AlbumArtist"))
            setv("genre", tags.get("Genre"))
            setv("date", tags.get("Date"))
            f.save()
            return True, "OK"

    except Exception as e:
        return False, f"Write error: {e}"

# =========================
# Core planning
# =========================

def find_cover_file(folder: str) -> Optional[str]:
    for name in CONFIG["cover_preferred_names"]:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    for f in os.listdir(folder):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            return os.path.join(folder, f)
    return None

def build_apply_plan(folder: str,
                     exif: List[Dict[str,Any]],
                     album_final: Dict[str,str],
                     track_final: Dict[str,Dict[str,str]],
                     renumber: bool,
                     skip_intro_00: bool,
                     rename_files: bool,
                     replace_rules: Optional[List[Dict[str,Any]]] = None,
                     strip_all_text: bool = False
                     ) -> Tuple[List[Dict[str,Any]], List[List[str]]]:
    plan = []
    csv_rows: List[List[str]] = []

    cover_file = find_cover_file(folder)
    cover_bytes = read_binary(cover_file) if cover_file else None

    # Normalize album-level fields *before* using them
    if album_final:
        for k, v in list(album_final.items()):
            if isinstance(v, str):
                album_final[k] = _apply_rules_to_text(v, k, replace_rules or [], strip_all_text)

    # replacement rules from config/CLI
    replace_rules = CONFIG.get("effective_replace_rules") or CONFIG.get("bulk_replacements") or []
    force_strip_all = bool(CONFIG.get("strip_all_text_tags"))

    # DiscNumber from album " - NN "
    discnumber = None
    m = re.search(r" - (\d{2,3})\b", album_final.get("Album","") or "")
    if m:
        discnumber = int(m.group(1))

    # Compute ordering: numeric filename order fallback
    exif_sorted = sorted(exif, key=lambda e: e.get("Track") or os.path.basename(e["SourceFile"]))

    next_track = 1
    for e in exif_sorted:
        sf = e["SourceFile"]
        base = os.path.basename(sf)
        ext = os.path.splitext(base)[1].lower()

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

        cur_title = str(e.get("Title", os.path.splitext(base)[0]))
        cur_comment = str(e.get("Comment", ""))
        cur_track = str(e.get("Track", ""))

        if sf in track_final:
            for k, v in track_final[sf].items():
                lk = k.lower()
        if lk.startswith("id3v2:"):
            k2 = k.split(":", 1)[1].lower()
        if k2 == "title":
            tags["Title"] = v
        elif k2 == "artist":
            tags["Artist"] = v
        elif k2 == "album":
            tags["Album"] = v
        elif lk == "title":
            tags["Title"] = v
        elif lk == "comment":
            tags["Comment"] = v
        elif lk == "performer":
            tags["Performer"] = v

        # Apply track-level overrides captured in track_final (existing code above)

        # Ensure Title/Comment fallbacks (existing code)
        tags.setdefault("Title", maybe_strip_diacritics(cur_title))
        tags.setdefault("Comment", maybe_strip_diacritics(cur_comment))

        # Apply global find/replace + optional full diacritics strip to all text tags
        for _k, _v in list(tags.items()):
            if isinstance(_v, str):
                tags[_k] = _apply_rules_to_text(_v, _k, replace_rules or [], strip_all_text)

        # apply bulk find/replace to all text tags
        for k in list(tags.keys()):
            if k in _TEXT_TAG_KEYS and isinstance(tags.get(k), (str, int)):
                v = tags.get(k)
                v = _apply_replacements_to_value(str(v), [r for r in replace_rules if not r.get("fields") or k in r.get("fields")], force_strip_all)
                tags[k] = v

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
            # If no explicit Track in tags, try infer from filename
            if cur_track:
                tags["Track"] = cur_track
            else:
                guess = infer_track_from_filename(base)
                tags["Track"] = guess or ""

        # Rename decision
        new_filename = None
        if rename_files:
            tnum = tags.get("Track") or ""
            tnum = str(tnum).split("/")[0] if tnum else ""
            tnum = (tnum.zfill(2) if tnum.isdigit() and len(tnum) < 2 else tnum)
            ttitle = clean_filename(tags.get("Title") or os.path.splitext(base)[0])
            new_filename = f"{(tnum + ' ') if tnum else ''}{ttitle}{ext}"
            if new_filename != base:
                csv_rows.append([sf, "FileName", base, new_filename, "RENAME", "from tags"])

        # CSV tag diffs
        for k in ("Title","Album","Artist","AlbumArtist","Performer","Translator","Genre","Date","DiscNumber","Track","Comment"):
            orig = str(e.get(k, "")) if e.get(k) is not None else ""
            sug = tags.get(k)
            if sug is None:
                continue
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
# Public API
# =========================
def fix_tags(folder: str,
             apply: bool = False,
             non_interactive: bool = True,
             force_rescan: bool = False,
             renumber: bool = False,
             rename_files: bool = False,
             strip_diacritics: bool = True,
             prefer_id3v2: bool = True,
             skip_intro_00: bool = True,
             overrides: Optional[Dict[str, str]] = None,
             replace_rules: Optional[List[Dict[str,Any]]] = None,
             strip_all_text: bool = False) -> Tuple[bool, str]:
    """
    Programmatic entry point.
    Returns (ok, message). If apply=False, just writes suggestions JSON/CSV/NFO.
    """
    if not os.path.isdir(folder):
        return False, f"Folder not found: {folder}"

    CONFIG["strip_diacritics_in_tags"] = strip_diacritics
    CONFIG["prefer_id3v2"] = prefer_id3v2
    CONFIG["skip_intro_00_when_renumber"] = skip_intro_00

    exif_data = run_exiftool(folder)
    if not exif_data:
        return False, "No supported audio metadata found (or exiftool missing)."

    existing = None if force_rescan else load_existing_json(folder)

    album_auto = suggest_album_level(folder, exif_data, overrides=overrides)
    album_user = existing["_album"]["user"] if existing and "_album" in existing and "user" in existing["_album"] else {}

    track_diffs = suggest_track_level(exif_data)

    suggestions = {
        "_scan_metadata": {
            "scan_date": datetime.now().isoformat(timespec="seconds"),
            "script_version": CONFIG["script_version"]
        },
        "_album": {"auto": album_auto["auto"], "user": album_user},
        "_cover": {},
        "tracks": track_diffs
    }

    # Always create/update NFO (raw dump & legacy)
    legacy = collect_legacy_text(folder)
    build_nfo(folder, suggestions, exif_data, legacy)

    # Non-interactive path: accept auto + track suggestions (no prompts)
    if non_interactive:
        album_final = dict(album_auto["auto"])
        # merge existing user overrides if present
        album_final.update(album_user or {})
        track_final = {sf: {k: v["suggested"] for k, v in diffs.items()} for sf, diffs in track_diffs.items()}

        # Save suggestions + CSV preview
        plan, csv_rows = build_apply_plan(folder, exif_data, album_final, track_final, renumber, skip_intro, rename_files,
                replace_rules=replace_rules,
                strip_all_text=strip_all_text)
        save_json(folder, suggestions)
        save_csv(folder, csv_rows)

        if not apply:
            return True, "Suggestions generated (non-interactive); no changes applied."

        # Apply
        failures = 0
        for item in plan:
            ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
            if not ok:
                failures += 1
            nf = item.get("new_filename")
            if ok and nf:
                old = item["file"]
                new = os.path.join(os.path.dirname(old), nf)
                if os.path.abspath(old) != os.path.abspath(new):
                    try:
                        shutil.move(old, new)
                    except Exception:
                        failures += 1
        return (failures == 0, "Applied with some failures." if failures else "Applied successfully.")

    # Interactive path: fall back to CLI front-end
    # (We keep interactive code in __main__ so API is clean.)
    return True, "Interactive mode requested from API; use the CLI to interact."

# =========================
# CLI (interactive or non-interactive)
# =========================

def ask_choice(prompt: str, choices: str = "a/k/m/s", default: str = "a") -> str:
    print(f"{prompt} [{choices}] (default {default})")
    ans = input("> ").strip().lower()
    if not ans:
        ans = default
    return ans

def review_album_level(album_auto: Dict[str,str], existing_user: Dict[str,str]) -> Dict[str,str]:
    print(f"\n{C.BOLD}=== ALBUM-LEVEL TAGS (inferred){C.RESET}")
    out = dict(existing_user) if existing_user else {}
    fields = ["Album", "Artist", "AlbumArtist", "Performer", "Translator", "Genre", "Date"]

    for i, key in enumerate(fields, start=1):
        code = f"a{i:02d}"
        orig = out.get(key, "")
        sug  = album_auto.get(key, "")

        print(cmag(f"{key} [{code}]"))
        print(f"- {cgray('original:')}\t{(orig if orig else '(none)')}")
        print(f"- {cgray('suggested:')}\t{(sug if sug else '(none)')}")
        print(f"- {cgray('manual:')}\t", end="")

        # default: accept if we have a suggestion; otherwise keep
        default_choice = "a" if sug else "k"
        ch = ask_choice("choose (a)ccept, (k)eep, (m)anual-from-original, (s)tart-from-suggestion", "a/k/m/s", default_choice)

        if ch == "a":
            out[key] = sug
            print(cgreen(sug if sug else "(none)"))
        elif ch == "k":
            out[key] = orig or sug
            print(cgreen(out[key] if out[key] else "(none)"))
        elif ch == "m":
            seed = orig if orig else ""
            nv = input(f"  enter new value (start original='{seed}'): ")
            out[key] = nv if nv is not None and nv != "" else seed
            print(cgreen(out[key] if out[key] else "(none)"))
        elif ch == "s":
            seed = sug if sug else ""
            nv = input(f"  enter new value (start suggestion='{seed}'): ")
            out[key] = nv if nv is not None and nv != "" else seed
            print(cgreen(out[key] if out[key] else "(none)"))

    return out

def review_tracks(track_sugs: Dict[str,Dict[str,Dict[str,str]]]) -> Dict[str,Dict[str,str]]:
    print(f"\n{C.BOLD}=== TRACK-LEVEL DIFFERENCES ONLY{C.RESET}")
    final = {}
    for idx, (sf, tags) in enumerate(sorted(track_sugs.items()), start=1):
        print(ccyan(f"--- {os.path.basename(sf)} --- ({idx})"))
        final[sf] = {}
        for t_idx, (tag, info) in enumerate(tags.items(), start=1):
            code = f"t{idx:02d}.{t_idx:02d}"
            orig = info["original"]
            sug  = info["suggested"]
            src  = info["source"]

            # Build pieces explicitly; avoid nested f-string conversions
            left = f"{tag:<18} {str(orig):<40} -> "
            right = cyellow(str(sug))
            src_txt = cgray(f"[{src}]")

            print(cmag(code.ljust(8)), left + right, src_txt)
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

def find_cover_path(folder: str) -> Optional[str]:
    for name in CONFIG["cover_preferred_names"]:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    for f in os.listdir(folder):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            return os.path.join(folder, f)
    return None

def build_and_write_nfo(folder: str, suggestions: Dict[str,Any], exif_data: List[Dict[str,Any]], legacy: List[Tuple[str,str,str]]) -> None:
    build_nfo(folder, suggestions, exif_data, legacy)

def build_suggestions(folder: str, force: bool, overrides: Optional[Dict[str,str]]):
    print(f"{C.BOLD}Scanning folder:{C.RESET} {folder}")
    exif_data = run_exiftool(folder)
    if not exif_data:
        print(cred("No supported audio metadata found or exiftool missing."), file=sys.stderr)
        sys.exit(2)

    existing = None if force else load_existing_json(folder)

    album = suggest_album_level(folder, exif_data, overrides=overrides)
    album_user = existing["_album"]["user"] if existing and "_album" in existing and "user" in existing["_album"] else {}

    track_diffs = suggest_track_level(exif_data)

    suggestions = {
        "_scan_metadata": {
            "scan_date": datetime.now().isoformat(timespec="seconds"),
            "script_version": CONFIG["script_version"]
        },
        "_album": {"auto": album["auto"], "user": album_user},
        "_cover": {},
        "tracks": track_diffs
    }
    legacy = collect_legacy_text(folder)
    return suggestions, exif_data, legacy

def main() -> int:
    check_dependencies()

    ap = argparse.ArgumentParser(description="Analyze and (optionally) retag audiobook folders.")
    ap.add_argument("folder", help="Target folder")

    # Behavior
    ap.add_argument("--force", action="store_true", help="Ignore previous _tags_suggestions.json")
    ap.add_argument("--apply", action="store_true", help="Apply suggestions without additional confirmation")
    ap.add_argument("--non-interactive", action="store_true", help="Accept auto + track suggestions (no prompts)")
    ap.add_argument("--renumber", action="store_true", help="Renumber tracks (skips 00/intro if configured)")
    ap.add_argument("--no-skip-intro", action="store_true", help="Do not skip '00/intro/uvod' when renumbering")
    ap.add_argument("--rename", action="store_true", help="Rename files from tags after writing")
    ap.add_argument("--no-strip-diacritics", action="store_true", help="Keep diacritics in final tag values")
    ap.add_argument("--prefer-id3v1", action="store_true", help="Prefer ID3v1 when v1/v2 differ (default: prefer v2)")

    # Album-level overrides (CLI)
    ap.add_argument("--set-author", dest="set_author", default=None)
    ap.add_argument("--set-year", dest="set_year", default=None)
    ap.add_argument("--set-album", dest="set_album", default=None)
    ap.add_argument("--set-performer", dest="set_performer", default=None)
    ap.add_argument("--set-translator", dest="set_translator", default=None)
    ap.add_argument("--genre", dest="set_genre", default=None)

    # Bulk text replacement (new)
    ap.add_argument(
        "--replace",
        action="append",
        metavar="FROM=>TO",
        help="Replace text in all tags. Repeatable."
    )
    ap.add_argument(
        "--replace-file",
        help="JSON file with rules: [{\"pattern\":\"FROM\",\"replacement\":\"TO\",\"regex\":false,\"fields\":[\"Title\",\"Album\",...]}]"
    )
    ap.add_argument(
        "--strip-diacritics-all",
        action="store_true",
        help="Force diacritics removal from ALL text tags at write time"
    )

    # Downloader-provided overrides file (optional)
    ap.add_argument("--overrides", help="Path to JSON file with pre-filled metadata overrides (from downloader)")

    args = ap.parse_args()

    # --- Apply CLI toggles to global CONFIG ---
    CONFIG["skip_intro_00_when_renumber"] = not args.no_skip_intro
    CONFIG["strip_diacritics_in_tags"] = not args.no_strip_diacritics
    CONFIG["prefer_id3v2"] = not args.prefer_id3v1

    # --- Load overrides (from file) ---
    file_overrides = {}
    if args.overrides:
        try:
            with open(args.overrides, "r", encoding="utf-8") as f:
                file_overrides = json.load(f)
            print(f"Loaded overrides from {args.overrides}")
        except Exception as e:
            print(f"Could not load overrides file: {e}")
            file_overrides = {}

    # --- Build CLI overrides dict (non-empty only) ---
    cli_overrides = {
        "Artist": args.set_author,
        "AlbumArtist": args.set_author,
        "Album": args.set_album,
        "Performer": args.set_performer,
        "Translator": args.set_translator,
        "Genre": args.set_genre,
        "Date": args.set_year,
    }
    cli_overrides = {k: v for k, v in cli_overrides.items() if v}

    # Merge file + CLI overrides (CLI wins where present)
    merged_overrides = {**file_overrides, **cli_overrides}

    # --- Build replacement rules list (CONFIG + file + CLI) ---
    def _load_replace_file(pth: str):
        try:
            with open(pth, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                print(cred(f"--replace-file must be a JSON list of rule objects, got: {type(data)}"))
                return []
            return data
        except Exception as e:
            print(cred(f"Could not read --replace-file: {e}"))
            return []

    rules_from_file = _load_replace_file(args.replace_file) if args.replace_file else []
    rules_from_cli = []
    for r in (args.replace or []):
        if "=>" not in r:
            print(cred(f"--replace must look like 'FROM=>TO', got: {r}"))
            continue
        a, b = r.split("=>", 1)
        rules_from_cli.append({"pattern": a, "replacement": b, "regex": False, "fields": []})

    replace_rules = (CONFIG.get("bulk_replacements") or []) + rules_from_file + rules_from_cli
    force_strip_all = args.strip_diacritics_all

    print(f"Scanning folder: {args.folder}")

    # Non-interactive path (no prompts)
    if args.non_interactive:
        ok, msg = fix_tags(
            args.folder,
            apply=args.apply,
            non_interactive=True,
            force_rescan=args.force,
            renumber=args.renumber,
            rename_files=args.rename,
            strip_diacritics=CONFIG["strip_diacritics_in_tags"],
            prefer_id3v2=CONFIG["prefer_id3v2"],
            skip_intro_00=CONFIG["skip_intro_00_when_renumber"],
            overrides=merged_overrides,
            replace_rules=replace_rules,
            strip_all_text_tags=force_strip_all,
        )
        print(msg)
        return 0 if ok else 1

    # Interactive path
    suggestions, exif_data, legacy = build_suggestions(args.folder, force=args.force, overrides=merged_overrides)
    build_nfo(args.folder, suggestions, exif_data, legacy)
    interactive_flow(
        args.folder,
        suggestions,
        exif_data,
        renumber=args.renumber,
        skip_intro=CONFIG["skip_intro_00_when_renumber"],
        rename_files=args.rename,
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())