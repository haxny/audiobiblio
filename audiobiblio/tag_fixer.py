#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tag_fixer.py

ONE-IN-ALL TOOL for audiobook tag analysis and correction.
- Importable API: fix_tags(folder, ...)
- CLI with interactive or non-interactive review
- Safe by default: no file writes unless --apply (or apply=True in API)

Features:
- Album-level suggestions from folder name, series index, comments
- Track-level fixes (encoding, leading numbers, ID3 v1/v2 reconcile)
- Optional diacritics stripping
- Optional renumber + rename
- Bulk find/replace across all tags (CONFIG, --replace, --replace-file)
"""

from __future__ import annotations

import os
import re
import sys
import csv
import json
import shlex
import shutil
import argparse
import subprocess
import unicodedata
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# =========================
# Config (defaults)
# =========================

CONFIG: Dict[str, Any] = {
    "strip_diacritics_in_tags": True,     # normal suggestion/tag values without diacritics
    "prefer_id3v2": True,                 # prefer v2 when v1/v2 differ
    "skip_intro_00_when_renumber": True,  # skip files like "00 ..." when renumbering
    "default_genre": "Audiokniha",
    "supported_audio_exts": (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac"),
    "cover_preferred_names": ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"),
    "script_version": "3.1",
}

# Built-in bulk replacements (edit freely)
CONFIG["bulk_replacements"] = [
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
# Dependencies
# =========================

def check_dependencies() -> None:
    from shutil import which
    if which("exiftool") is None:
        print(cred("Missing dependency: exiftool (install: brew install exiftool)"))
        sys.exit(2)
    try:
        import mutagen  # noqa: F401
    except Exception:
        print(cred("Missing dependency: mutagen (pip install mutagen)"))
        sys.exit(2)

# =========================
# Helpers
# =========================

# ---------- Mojibake / cp1250 helpers + m3u reader ----------

def _looks_mojibake(s: str) -> bool:
    # common Latin-1/UTF-8 debris, Windows-1250 artifacts
    bad_fragments = ("Â", "Ã", "", "", "", "", "§", "¡", "Ø", "è", "ì", "ò", "ù")
    return any(b in s for b in bad_fragments)

def _guess_cp1250_fix(s: str) -> Optional[str]:
    """
    Try to reverse 'text that was cp1250 bytes but decoded as latin-1/UTF-8 garbage'.
    Heuristic: encode as latin-1 bytes, decode as cp1250 text. Keep if it looks better.
    """
    try:
        repaired = s.encode("latin-1", "ignore").decode("cp1250", "ignore")
        if repaired and repaired != s and (_looks_mojibake(s) or not _looks_mojibake(repaired)):
            return repaired
    except Exception:
        pass
    return None

def _maybe_fix_cp1250(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    fixed = _guess_cp1250_fix(s)
    return fixed or s

def _load_m3u_titles(folder: str) -> Dict[str, str]:
    """
    Read .m3u / .m3u8 next to audio files and map filename stem -> Title from #EXTINF.
    Handles cp1250 mojibake heuristically.
    """
    titles: Dict[str, str] = {}
    if not folder or not os.path.isdir(folder):
        return titles

    paths = []
    for name in os.listdir(folder):
        ln = name.lower()
        if ln.endswith(".m3u") or ln.endswith(".m3u8"):
            paths.append(os.path.join(folder, name))

    for p in paths:
        try:
            # Try UTF‑8 first
            raw = open(p, "r", encoding="utf-8", errors="strict").read()
        except Exception:
            # Fallback: latin-1 to bytes → cp1250 to text
            try:
                raw_bytes = open(p, "rb").read()
                try:
                    raw = raw_bytes.decode("utf-8", errors="strict")
                except Exception:
                    raw = raw_bytes.decode("latin-1", errors="ignore").encode("latin-1").decode("cp1250", errors="ignore")
            except Exception:
                continue

        last_title = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.upper().startswith("#EXTINF:"):
                # take the bit after the last comma
                after = line.split(",", 1)[-1].strip()
                last_title = _maybe_fix_cp1250(after)
                continue
            if not line.startswith("#"):
                stem = os.path.splitext(os.path.basename(line))[0]
                if last_title:
                    titles[stem] = last_title
                    last_title = None
    return titles
# ---------- end helpers ----------

def strip_diacritics(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def maybe_strip_diacritics(s: str) -> str:
    return strip_diacritics(s) if CONFIG["strip_diacritics_in_tags"] else s

# --- Helpers for better mojibake / diacritics detection ---

def _load_m3u_titles(folder: str) -> dict[str, str]:
    """
    Look for .m3u or .m3u8 in the folder. Build a mapping:
       basename-without-ext -> title from #EXTINF line
    If the file itself is mojibaked (CP1250 read as Latin‑1), try to repair lines.
    """
    import glob
    result: dict[str, str] = {}
    for p in glob.glob(os.path.join(folder, "*.m3u*")):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith("#EXTINF:"):
                # Title after the comma
                try:
                    title = line.split(",", 1)[1].strip()
                except Exception:
                    title = ""
                # Try repair title from cp1250 mojibake if needed
                fixed = _guess_cp1250_fix(title)
                if fixed:
                    title = fixed

                # The next non-comment line should be the file name
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith("#"):
                    j += 1
                if j < len(lines):
                    fname = os.path.basename(lines[j].strip())
                    base_noext = os.path.splitext(fname)[0]
                    result[base_noext] = title
    return result

def ascii_only(s: str) -> bool:
    return all(ord(c) < 128 for c in s)

def has_diacritics(s: str) -> bool:
    # returns True if removing diacritics changes the string
    return strip_diacritics(s) != s

def core_title_from_name(name: str) -> str:
    """
    Take a file name or title and remove extension and leading track number
    like '01 - ', '14_', '3. ' etc., to compare the core text fairly.
    """
    base = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r"^\s*\d{1,3}\s*[-._:; ]?\s*", "", base).strip()

def looks_mojibake(s: str) -> bool:
    """
    Extended detection:
      - classic UTF-8/Windows-125x garbage (Ã, Å, Â, etc.) - extended list of Central European garble
      - the replacement character �
      - a '?' embedded between letters (e.g., 'Mu?ete')
    """
    if not s:
        return False
    
    bad_fragments = [
        "È", "Ø", "ø", "Å", "Ã", "œ", "Å¡", "Å½", "Ã¡", "Ã©", "Ã¨",
        "Ãº", "Ã±", "Ã¾", "Â", "ù", "ì", "ò", "æ", "ø", "Å™", "Å¯",
        "Å¡", "Å¾", "Ä›", "ÄŒ", "Ä", "Ä"
    ]
    if any(bad in s for bad in bad_fragments):
        return True
    # heuristic: lots of question marks *inside* words (not punctuation)
    # e.g., "mu?ce", "Povy?il"
    if re.search(r"[A-Za-z]\?[A-Za-z]", s):
        return True
    return False

def try_windows1250_fix(s: str) -> Optional[str]:
    """
    Attempt to repair typical CP1250 mojibake that was decoded as latin-1/UTF-8.
    Strategy: re-encode with latin-1 bytes, then decode as cp1250.
    Return repaired string if it looks better; else None.
    """
    try:
        # encode back to bytes 1:1, then decode as cp1250
        raw = s.encode("latin-1", errors="strict")
        fixed = raw.decode("cp1250", errors="strict")
    except Exception:
        return None

    # Heuristic: if original looked mojibake and fixed doesn't, trust it
    if looks_mojibake(s) and not looks_mojibake(fixed):
        return fixed
    # Or if fixed simply has more letters with diacritics / fewer mojibake marks.
    bad_chars = "ùìòøæœÂÃÅžŽžŠšÝýÈéÌíÒóÙú"
    def score(txt: str) -> int:
        return sum(ch in bad_chars for ch in txt)
    if score(fixed) < score(s):
        return fixed
    return None

def fix_encoding_from_filename(tag_value: str, filename: str) -> Optional[str]:
    """
    If a tag looks mojibake, try to repair it. Priority:
    1) CP1250 repair (latin-1 -> cp1250 roundtrip).
    2) If filename base looks clean, use that (after stripping a leading track number).
    3) Otherwise return a diacritics-stripped version of the original (last resort).
    Returns None if no change is suggested.
    """
    if not tag_value:
        return None

    if not looks_mojibake(tag_value):
        return None  # keep original; nothing to repair

    # 1) Try cp1250 repair
    repaired = try_windows1250_fix(tag_value)
    if repaired and not looks_mojibake(repaired):
        return maybe_strip_diacritics(repaired)

    # 2) Fallback: try to use filename base if it looks clean
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"^\s*\d+\s*[-_.]?\s*", "", base).strip()
    if base and not looks_mojibake(base):
        return maybe_strip_diacritics(base)

    # 3) Last resort: at least strip diacritics of the mojibake (stabilizes ASCII)
    return maybe_strip_diacritics(tag_value)

    return None

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
        print(cred("Could not parse ExifTool JSON output."), file=sys.stderr)
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
    Accept patterns:
      "Author - (YYYY) Title"
      "Author - Title"
    Fallback: whole folder name as 'title'.
    """
    folder_name = os.path.basename(folder).strip()
    author, year, title = None, None, None

    m = re.match(r"^(?P<author>.+?)\s*-\s*\((?P<year>\d{4})\)\s*(?P<title>.+)$", folder_name)
    if m:
        return {"author": m.group("author").strip(),
                "year": m.group("year").strip(),
                "title": m.group("title").strip()}

    m = re.match(r"^(?P<author>.+?)\s*-\s*(?P<title>.+)$", folder_name)
    if m:
        return {"author": m.group("author").strip(),
                "year": None,
                "title": m.group("title").strip()}

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
    name = os.path.splitext(base)[0]
    m = re.match(r"^\s*(\d{1,3})\s*([.\-–_:; ]|$)", name)
    if m:
        num = m.group(1)
        if len(num) == 1:
            return num.zfill(2)
        return num
    m = re.match(r"^\s*(\d{1,3})\s*(?:d[ií]l)\b", name, flags=re.IGNORECASE)
    if m:
        num = m.group(1)
        return num.zfill(2) if len(num) < 2 else num
    return None

def collect_legacy_text(folder: str) -> List[Tuple[str, str, str]]:
    legacy: List[Tuple[str, str, str]] = []
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
                pass
    return legacy

def read_binary(p: str) -> Optional[bytes]:
    try:
        with open(p, "rb") as f:
            return f.read()
    except Exception:
        return None

# --- Encoding/playlist helpers ----------------------------------------------

# --- cp1250 + playlist mojibake helpers -----------------------------------------------

# -------------------------
# Mojibake & playlist helpers
# -------------------------
def _looks_cp1250_mojibake(s: str) -> bool:
    """
    Heuristic: string contains the usual CP1250->UTF8 mojibake markers.
    Examples: '‚', 'ˇ', 'Å', 'Ã', 'Â' etc.
    """
    if not s:
        return False
    suspects = ["‚", "ˇ", "Å", "Ã", "Â", "ø", "Ø", "Æ", "æ", "¤", "", "", "", "ý", "", ""]
    return any(ch in s for ch in suspects)

def _guess_cp1250_fix(s: str) -> str:
    """
    Try to repair CP1250 mojibake by round‑tripping common wrong encodings.
    If nothing helps, return the original.
    """
    if not s:
        return s

    # Short‑circuit: only try if it looks broken
    if not _looks_cp1250_mojibake(s):
        return s

    candidates = set()

    # Typical mojibake path: s is already wrongly decoded as UTF‑8, try interpreting
    # its bytes as latin-1, cp1252 and re‑decode as cp1250 / utf‑8.
    try:
        candidates.add(s.encode("latin-1", "ignore").decode("cp1250", "ignore"))
    except Exception:
        pass
    try:
        candidates.add(s.encode("cp1252", "ignore").decode("cp1250", "ignore"))
    except Exception:
        pass
    try:
        # Sometimes double-encoded UTF-8 shows up as Ã… etc.
        candidates.add(s.encode("latin-1", "ignore").decode("utf-8", "ignore"))
    except Exception:
        pass

    # Choose the candidate that has the most Czech diacritics (rough heuristic)
    def score(txt: str) -> int:
        cz = "ěščřžýáíéÉÁÍÝŽŘČŠĚďťňúůŮÚóÓĺĹľĽňŇ"
        return sum(1 for ch in txt if ch in cz)

    best = max(candidates or {s}, key=score)
    # If best still looks mojibake, fall back to original
    return best if not _looks_cp1250_mojibake(best) else s

def _load_m3u_titles(folder: str) -> dict:
    """
    Scan .m3u/.m3u8 in folder and build a dict:
      key = basename without extension (track filename stem)
      value = title text from #EXTINF line (after the comma)
    We also try to fix CP1250 mojibake in those titles.
    """
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    for name in os.listdir(folder):
        if not name.lower().endswith((".m3u", ".m3u8")):
            continue
        p = os.path.join(folder, name)
        try:
            # try utf-8 first
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except Exception:
            continue

        last_extinf_title = None
        for line in lines:
            if line.startswith("#EXTINF:"):
                # format: #EXTINF:123,ARTIST - Title text
                # we keep everything after the first comma
                if "," in line:
                    last_extinf_title = line.split(",", 1)[1].strip()
                    last_extinf_title = _guess_cp1250_fix(last_extinf_title)
                else:
                    last_extinf_title = None
            elif line and not line.startswith("#"):
                # This line is the filename the previous EXTINF describes
                stem = os.path.splitext(os.path.basename(line.strip()))[0]
                if last_extinf_title:
                    out[stem] = last_extinf_title
                last_extinf_title = None
    return out

# =========================
# Replacement helpers
# =========================

def _compile_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(rule)
    r.setdefault("regex", False)
    r.setdefault("fields", [])
    if r["regex"]:
        try:
            r["_re"] = re.compile(r["pattern"])
        except re.error as e:
            print(cred(f"Bad regex in replacement rule '{r}': {e}"))
            r["_re"] = None
    return r

def apply_replacements_to_text(value: Optional[str],
                               rules: List[Dict[str, Any]],
                               strip_all: bool = False) -> Optional[str]:
    if value is None:
        return None
    out = value
    for r in rules:
        # no field filtering here (done by caller), we just apply
        if r.get("regex") and r.get("_re"):
            out = r["_re"].sub(r.get("replacement", ""), out)
        else:
            pat = r.get("pattern", "")
            rep = r.get("replacement", "")
            if pat:
                out = out.replace(pat, rep)
    if strip_all:
        out = strip_diacritics(out)
    return out

def apply_replacements_to_tags(tags: Dict[str, Any],
                               rules: List[Dict[str, Any]],
                               strip_all: bool = False) -> Dict[str, Any]:
    if not rules and not strip_all:
        return tags
    out = dict(tags)
    for k, v in list(out.items()):
        if v is None:
            continue
        s = str(v)
        # apply only rules that are global or explicitly include this field
        selected = []
        for r in rules:
            fields = r.get("fields") or []
            if not fields or k in fields:
                selected.append(r)
        new_val = apply_replacements_to_text(s, selected, strip_all=strip_all)
        out[k] = new_val
    return out

# =========================
# Suggestions builder
# =========================

def summarize_missing_and_suggested(album_auto: Dict[str, str],
                                    exif_data: List[Dict[str,Any]],
                                    folder: str) -> None:
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
        what = []
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

def suggest_album_level(folder: str,
                        exif: List[Dict[str,Any]],
                        overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    overrides = overrides or {}
    fld = parse_folder_metadata(folder)
    series, series_idx = detect_series_from_parent(folder)

    def norm(x: Optional[str]) -> Optional[str]:
        return maybe_strip_diacritics(x) if x else x

    # Collect hints
    artists = set()
    albums  = set()
    genres  = set()
    comments = []

    for e in exif:
        if e.get("Artist"): artists.add(str(e["Artist"]))
        if e.get("Album"):  albums.add(str(e["Album"]))
        # pick up any "Genre"/"ItemList_Genre" etc.
        for k, v in e.items():
            if v is not None and k.lower().endswith("genre"):
                genres.add(str(v))
        if e.get("Comment"):
            comments.append(str(e["Comment"]))

    author_guess = fld.get("author")
    title_guess  = fld.get("title")
    year_guess   = fld.get("year")

    # Try to fix cp1250 mojibake from folder-derived bits
    if author_guess: author_guess = _maybe_fix_cp1250(author_guess)
    if title_guess:  title_guess  = _maybe_fix_cp1250(title_guess)

    # If no author in folder, fallback to present artist tag
    if not author_guess and artists:
        author_guess = _maybe_fix_cp1250(list(artists)[0])

    # Performer from comments (čte/cte)
    perf_guess = None
    for c in comments:
        m = re.search(r"(?:čte|cte)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            perf_guess = _maybe_fix_cp1250(m.group(1).strip())
            break

    # Translator from comments
    translator_guess = None
    for c in comments:
        m = re.search(r"(?:překlad|preklad|translated\s*by)\s*:\s*([^|/;\n]+)", c, flags=re.IGNORECASE)
        if m:
            translator_guess = _maybe_fix_cp1250(m.group(1).strip())
            break

    # Album guess (support Series - NN Title)
    if series and series_idx:
        bt = title_guess or ""
        if series.strip().lower() == (title_guess or "").strip().lower():
            album_guess = f"{series} - {series_idx:02d}"
        else:
            album_guess = f"{series} - {series_idx:02d} {bt}".strip()
    else:
        if albums:
            album_guess = _maybe_fix_cp1250(next(iter(albums)))
        else:
            album_guess = os.path.basename(folder)

    # Last pass: try cp1250 repair on album/author
    album_guess  = _maybe_fix_cp1250(album_guess)
    author_guess = _maybe_fix_cp1250(author_guess)

    # Overrides (accept both lower/title-case keys)
    def get_ovr(*keys):
        for k in keys:
            if k in overrides and overrides[k]:
                return overrides[k]
        return None

    artist_final       = get_ovr("artist", "Artist") or author_guess
    albumartist_final  = get_ovr("album_artist", "AlbumArtist") or author_guess
    album_final        = get_ovr("album", "Album") or album_guess
    performer_final    = get_ovr("performer", "Performer") or perf_guess
    translator_final   = get_ovr("translator", "Translator") or translator_guess
    genre_final        = (get_ovr("genre", "Genre")
                          or (next(iter(genres)) if genres else None)
                          or CONFIG["default_genre"])
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
            "why": "Album derived from folder/series + tag hints; cp1250 mojibake repaired; diacritics stripped per config."
        }
    }

def suggest_track_level(exif: List[Dict[str,Any]]) -> Dict[str, Dict[str,Dict[str,str]]]:
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    if not exif:
        return out

    # load playlist titles once (for cp1250-correct text)
    common_dir = os.path.dirname(exif[0].get("SourceFile", "") or "")
    m3u_titles = _load_m3u_titles(common_dir)

    for e in exif:
        sf = e.get("SourceFile")
        if not sf:
            continue
        track_sugs: Dict[str, Dict[str, str]] = {}

        # --- fields with possible mojibake / diacritics issues
        for tag in ("Title", "Comment", "Performer"):
            orig = str(e.get(tag, "")) if e.get(tag) is not None else ""
            if not orig:
                continue

            # Try to repair encoding via filename comparison (your existing heuristic)
            enc_fix = fix_encoding_from_filename(orig, sf)

            # If not fixed, try reading title candidate from playlist (stem-matched)
            base_noext = os.path.splitext(os.path.basename(sf))[0]
            if not enc_fix and base_noext in m3u_titles:
                candidate = m3u_titles[base_noext]
                if candidate and candidate != orig:
                    # run cp1250 fix on candidate and prefer it
                    enc_fix = _guess_cp1250_fix(candidate) or candidate

            # If filename and tag differ only by diacritics, prefer filename text
            if not enc_fix:
                base_clean = re.sub(r"^\s*\d{1,3}\s*[-_.]?\s*", "", base_noext)
                if base_clean and base_clean != orig:
                    import unicodedata
                    def simplify(x): return unicodedata.normalize("NFD", x).encode("ascii", "ignore").decode("ascii")
                    if simplify(base_clean) == simplify(orig):
                        enc_fix = base_clean

            # Finally, try direct cp1250 mojibake repair on the tag value
            if not enc_fix and _looks_mojibake(orig):
                try_fix = _guess_cp1250_fix(orig)
                if try_fix and try_fix != orig:
                    enc_fix = try_fix

            if enc_fix and enc_fix != orig:
                track_sugs[tag] = {
                    "original": orig,
                    "suggested": enc_fix,
                    "source": "Mojibake/diacritics repair (filename/playlist/cp1250)"
                }

        # --- use the (possibly repaired) title for “drop leading number”
        title = str(e.get("Title", "")) if e.get("Title") is not None else ""
        effective_title = track_sugs.get("Title", {}).get("suggested", title)
        track = str(e.get("Track", "")) if e.get("Track") is not None else ""
        if effective_title and track:
            m = re.match(r"^\s*\d{1,3}\s*[-_.]?\s*(.*)$", effective_title)
            if m:
                sug = maybe_strip_diacritics(m.group(1).strip())
                if sug and sug != effective_title:
                    track_sugs["Title"] = {
                        "original": title,
                        "suggested": sug,
                        "source": "Title: drop leading number (track set)"
                    }

        # --- ID3v1 vs ID3v2 disagreement hint
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

        # --- Normalize Track/Disc like "3/18" → "03", "1/1" → "1"
        if e.get("Track") and isinstance(e["Track"], (int, str)):
            trk = str(e["Track"])
            if "/" in trk:
                first = trk.split("/", 1)[0]
                norm = first.zfill(2) if first.isdigit() and len(first) < 2 else first
                if norm != trk:
                    track_sugs["Track"] = {"original": trk, "suggested": norm, "source": "Normalize Track number"}

        if e.get("Disc") and isinstance(e["Disc"], (int, str)):
            dsc = str(e["Disc"])
            if "/" in dsc:
                first = dsc.split("/", 1)[0]
                if first != dsc:
                    track_sugs["DiscNumber"] = {"original": dsc, "suggested": first, "source": "Normalize Disc number"}

        if track_sugs:
            out[sf] = track_sugs

    return out

# =========================
# NFO builder
# =========================

def build_nfo(folder: str, suggestions: Dict[str,Any], exif_data: List[Dict[str,Any]],
              legacy_notes: List[Tuple[str,str,str]]) -> None:
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
            if tags.get("Performer"):
                id3.setall("TXXX", [TXXX(encoding=3, desc="PERFORMER", text=str(tags["Performer"]))])
            if tags.get("DiscNumber"):
                set_text(TPOS, "TPOS", str(tags["DiscNumber"]))
            if tags.get("Translator"):
                id3.setall("TXXX", [TXXX(encoding=3, desc="TRANSLATOR", text=str(tags["Translator"]))])
            if tags.get("Comment"):
                id3.setall("COMM", [])
                id3.add(COMM(encoding=3, lang="eng", desc="", text=str(tags["Comment"])))
            if tags.get("Track"):
                set_text(TRCK, "TRCK", str(tags["Track"]))
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
            def setv(k,v):
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
                     replace_rules: List[Dict[str, Any]],
                     force_strip_all: bool) -> Tuple[List[Dict[str,Any]], List[List[str]]]:

    plan: List[Dict[str, Any]] = []
    csv_rows: List[List[str]] = []

    cover_file = find_cover_file(folder)
    cover_bytes = read_binary(cover_file) if cover_file else None

    # DiscNumber from album " - NN "
    discnumber = None
    m = re.search(r" - (\d{2,3})\b", album_final.get("Album","") or "")
    if m:
        discnumber = int(m.group(1))

    exif_sorted = sorted(exif, key=lambda e: e.get("Track") or os.path.basename(e["SourceFile"]))

    next_track = 1
    for e in exif_sorted:
        sf = e["SourceFile"]
        base = os.path.basename(sf)
        ext = os.path.splitext(base)[1].lower()

        tags: Dict[str, Any] = {
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
            if cur_track:
                tags["Track"] = cur_track
            else:
                guess = infer_track_from_filename(base)
                tags["Track"] = guess or ""

        # Apply bulk replacements (+ optional strip all)
        tags = apply_replacements_to_tags(tags, replace_rules, strip_all=force_strip_all)

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
            sug  = tags.get(k)
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
             replace_rules: Optional[List[Dict[str, Any]]] = None,
             strip_all_text_tags: bool = False) -> Tuple[bool, str]:
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

    legacy = collect_legacy_text(folder)
    build_nfo(folder, suggestions, exif_data, legacy)

    # prepare replace rules
    compiled_rules = [ _compile_rule(r) for r in (replace_rules or []) ]

    if non_interactive:
        album_final = dict(album_auto["auto"])
        album_final.update(album_user or {})
        track_final = {sf: {k: v["suggested"] for k, v in diffs.items()} for sf, diffs in track_diffs.items()}

        plan, csv_rows = build_apply_plan(
            folder, exif_data, album_final, track_final,
            renumber, CONFIG["skip_intro_00_when_renumber"], rename_files,
            replace_rules=compiled_rules, force_strip_all=strip_all_text_tags
        )
        save_json(folder, suggestions)
        save_csv(folder, csv_rows)

        if not apply:
            return True, "Suggestions generated (non-interactive); no changes applied."

        failures = 0
        for item in plan:
            ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
            if not ok:
                failures += 1
                continue
            nf = item.get("new_filename")
            if nf:
                old = item["file"]
                new = os.path.join(os.path.dirname(old), nf)
                if os.path.abspath(old) != os.path.abspath(new):
                    try:
                        shutil.move(old, new)
                    except Exception:
                        failures += 1
        return (failures == 0, "Applied with some failures." if failures else "Applied successfully.")

    # interactive_flow (kept minimal; ENTER=accept)
    interactive_flow(
        args.folder,
        suggestions,
        exif_data,
        renumber=args.renumber,
        skip_intro=CONFIG["skip_intro_00_when_renumber"],
        rename_files=args.rename,
        tracks_only=args.tracks_only,
    )

    cover_path = find_cover_file(folder)
    if cover_path:
        suggestions["_cover"] = {"CoverFile": os.path.basename(cover_path), "CoverPath": cover_path}

    plan, csv_rows = build_apply_plan(
        folder, exif_data, album_final, track_final,
        renumber, CONFIG["skip_intro_00_when_renumber"], rename_files,
        replace_rules=compiled_rules, force_strip_all=strip_all_text_tags
    )

    print(f"\n{C.BOLD}=== APPLY PREVIEW ==={C.RESET}")
    for i, item in enumerate(plan, 1):
        base = os.path.basename(item["file"])
        t = item["tags"]
        rn = ""
        if item.get("new_filename") and item["new_filename"] != base:
            rn = f"  → rename: {cgreen(item['new_filename'])}"
        tr = t.get('Track') or ''
        ttl = t.get('Title') or ''
        print(f"{i:>3}. {ccyan(base)}  |  Track: {tr:<3}  |  Title: {ttl}{rn}")

    save_json(folder, suggestions)
    save_csv(folder, csv_rows)

    from_choice = input(cyellow("\nApply all changes to files? [Y/n]: ")).strip().lower()
    if from_choice in ("", "y", "yes"):
        failures = 0
        for item in plan:
            ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
            if not ok:
                print(cred(f"Write failed: {item['file']} → {msg}"))
                failures += 1
                continue
            nf = item.get("new_filename")
            if nf:
                old = item["file"]
                new = os.path.join(os.path.dirname(old), nf)
                if os.path.abspath(old) != os.path.abspath(new):
                    try:
                        shutil.move(old, new)
                    except Exception as e:
                        print(cred(f"Rename failed: {old} → {new}: {e}"))
                        failures += 1
        if failures:
            print(cred(f"Done with {failures} failures."))
        else:
            print(cgreen("All changes applied successfully."))
    else:
        print(cgray("No changes applied. Suggestions, CSV, and NFO saved."))

    return True, "Done"

# =========================
# Interactive helpers
# =========================

def ask_choice(prompt: str, choices: str = "a/k/m/s", default: str = "a", allow_free_text: bool = True) -> str:
    print(f"{prompt} [{choices}] (default {default})")
    ans = input("> ").strip()
    if ans == "":
        return default
    # If user typed one of the single-letter choices, return it.
    low = ans.lower()
    if low in [c.strip() for c in choices.split("/")]:
        return low
    # Otherwise, if free-text edits are allowed, return as a manual value token
    # We encode manual text as 'manual:<value>' so callers can detect it.
    if allow_free_text:
        return f"manual:{ans}"
    return low

def review_album_level(album_auto: Dict[str, str], existing_user: Dict[str, str]) -> Dict[str, str]:
    """
    Interactive album-level review.
    - ENTER accepts the suggested value (if any), otherwise keeps the current/original.
    - 'k' keeps the current/original
    - 'm' manual edit starting from ORIGINAL
    - 's' manual edit starting from SUGGESTED
    - 'aa' accept ALL remaining fields' suggested values and finish
    """
    print(f"\n{C.BOLD}=== ALBUM-LEVEL TAGS (inferred){C.RESET}")
    out = dict(existing_user) if existing_user else {}

    fields = ["Album", "Artist", "AlbumArtist", "Performer", "Translator", "Genre", "Date"]

    i = 0
    while i < len(fields):
        key = fields[i]
        code = f"a{i+1:02d}"

        orig = out.get(key, "")                  # what we already had (from previous runs)
        sug  = album_auto.get(key, "")           # suggestion we computed now

        print(cmag(f"{key} [{code}]"))
        print(f"- {cgray('original:')}\t{(orig if orig else '(none)')}")
        print(f"- {cgray('suggested:')}\t{(sug if sug else '(none)')}")
        # Hint includes 'aa'
        print(f"- {cgray('manual:')}\t(type to edit)  "
              + cgray("choices: ENTER=accept, k=keep, m=manual(orig), s=manual(sugg), aa=accept all remaining"))
        
        ans = input("> ").strip()

        # --- Quick bulk accept: accept suggested for *this and all remaining* ---
        if ans.lower() == "aa":
            for j in range(i, len(fields)):
                k2 = fields[j]
                s2 = album_auto.get(k2, "")
                out[k2] = s2 if s2 else (out.get(k2, "") or "")
                # (optional) brief echo so the user sees what's happening
                print(cgreen(f"  ✓ {k2}: {(out[k2] if out[k2] else '(none)')}"))
            break  # we're done with album-level
        # --- ENTER: default → accept if we have a suggestion; otherwise keep ---
        elif ans == "":
            if sug:
                out[key] = sug
            else:
                out[key] = orig
            print(cgreen(out[key] if out[key] else "(none)"))
        elif ans.lower() == "k":
            out[key] = orig or sug
            print(cgreen(out[key] if out[key] else "(none)"))
        elif ans.lower() == "m":
            seed = orig if orig else ""
            nv = input(f"  enter new value (start original='{seed}'): ").strip()
            out[key] = nv if nv != "" else seed
            print(cgreen(out[key] if out[key] else "(none)"))
        elif ans.lower() == "s":
            seed = sug if sug else ""
            nv = input(f"  enter new value (start suggestion='{seed}'): ").strip()
            out[key] = nv if nv != "" else seed
            print(cgreen(out[key] if out[key] else "(none)"))
        else:
            # If the user simply typed some text (no command), treat it as the new value
            out[key] = ans
            print(cgreen(out[key] if out[key] else "(none)"))

        i += 1

    return out

def review_tracks(track_sugs: Dict[str, Dict[str, Dict[str, str]]]) -> Dict[str, Dict[str, str]]:
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

            print(cmag(code.ljust(8)), f"{tag:<18} {str(orig):<40} -> {cyellow(str(sug))} {cgray('['+src+']')}")
            print(f"  hint: ENTER = accept suggested; type text to set custom; or use a/k/m/s")

            choice = ask_choice("  choose: (a)ccept, (k)eep, (m)anual-from-original, (s)tart-from-suggestion",
                                "a/k/m/s", "a", allow_free_text=True)

            if choice.startswith("manual:"):
                final[sf][tag] = choice.split(":", 1)[1]
                continue

            if choice == "a":
                final[sf][tag] = sug
            elif choice == "k":
                final[sf][tag] = orig
            elif choice == "m":
                seed = orig
                print(f"    start from ORIGINAL → {seed or '(empty)'}")
                nv = input("    edit (ENTER = keep seed): ").strip()
                final[sf][tag] = (nv if nv != "" else seed)
            elif choice == "s":
                seed = sug
                print(f"    start from SUGGESTED → {seed or '(empty)'}")
                nv = input("    edit (ENTER = keep seed): ").strip()
                final[sf][tag] = (nv if nv != "" else seed)
    return final

def interactive_flow(
    folder: str,
    suggestions: Dict[str, Any],
    exif_data: List[Dict[str, Any]],
    renumber: bool,
    skip_intro: bool,
    rename_files: bool,
    tracks_only: bool = False,
) -> None:
    """
    One place that drives the interactive session:
    - If tracks_only=True → skip album prompts and reuse last accepted album values
      (or AUTO if no user section exists yet).
    - Otherwise → prompt for album tags as before.
    Then always go through track suggestions and write/apply.
    """

    # 1) Preface / summary
    summarize_missing_and_suggested(suggestions["_album"]["auto"], exif_data, folder)

    # 2) Album-level review or reuse last accepted
    if tracks_only:
        # “reuse last accepted”: if user section exists, use it; else use AUTO
        album_final = dict(suggestions["_album"].get("user") or suggestions["_album"]["auto"])
    else:
        album_final = review_album_level(
            suggestions["_album"]["auto"],
            suggestions["_album"].get("user", {})
        )
        # persist for next run (so repeated runs start from what you accepted last time)
        suggestions["_album"]["user"] = album_final

    # 3) Track-level review (ENTER = accept in your review functions)
    track_final = review_tracks(suggestions["tracks"])

    # 4) Cover info (optional, if your helper exists)
    cover_path = find_cover_file(folder)
    if cover_path:
        suggestions["_cover"] = {
            "CoverFile": os.path.basename(cover_path),
            "CoverPath": cover_path,
        }

    # 5) Plan + CSV/JSON
    plan, csv_rows = build_apply_plan(
        folder,
        exif_data,
        album_final,
        track_final,
        renumber,
        skip_intro,
        rename_files,
        # if you wired bulk replacements & “strip all text” flags, pass them here;
        # otherwise just leave these out or keep defaults
        replace_rules=[],
        force_strip_all=False,
    )

    # Persist suggestions + report for the *next* run
    save_json(folder, suggestions)
    save_csv(folder, csv_rows)

    # 6) Apply prompt (your build_apply_plan already plans everything)
    from_choice = input(cyellow("\nApply all changes to files? [Y/n]: ")).strip().lower()
    if from_choice in ("", "y", "yes"):
        failures = 0
        for item in plan:
            ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
            if not ok:
                print(cred(f"Write failed: {item['file']} → {msg}"))
                failures += 1
                continue
            nf = item.get("new_filename")
            if nf:
                old = item["file"]
                new = os.path.join(os.path.dirname(old), nf)
                if os.path.abspath(old) != os.path.abspath(new):
                    try:
                        shutil.move(old, new)
                    except Exception as e:
                        print(cred(f"Rename failed: {old} → {new}: {e}"))
                        failures += 1
        if failures:
            print(cred(f"Done with {failures} failures."))
        else:
            print(cgreen("All changes applied successfully."))
    else:
        print(cgray("No changes applied. Suggestions and CSV saved."))

# =========================
# CLI
# =========================

def _load_replace_file(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for r in data:
                if isinstance(r, dict) and "pattern" in r:
                    rules.append({
                        "pattern": r["pattern"],
                        "replacement": r.get("replacement", ""),
                        "regex": bool(r.get("regex", False)),
                        "fields": list(r.get("fields", [])) if r.get("fields") else [],
                    })
        return rules
    except Exception as e:
        print(cred(f"Could not load replace file '{path}': {e}"))
        return []

def main() -> int:
    check_dependencies()
    ap = argparse.ArgumentParser(description="Analyze and (optionally) retag audiobook folders.")
    ap.add_argument("folder", help="Target folder")
    ap.add_argument("--force", action="store_true", help="Ignore previous _tags_suggestions.json")
    ap.add_argument("--apply", action="store_true", help="Apply suggestions without additional confirmation")
    ap.add_argument("--non-interactive", action="store_true", help="Accept auto + track suggestions (no prompts)")
    ap.add_argument("--renumber", action="store_true", help="Renumber tracks (skips 00/intro if configured)")
    ap.add_argument("--no-skip-intro", action="store_true", help="Do not skip '00/intro/uvod' when renumbering")
    ap.add_argument("--rename", action="store_true", help="Rename files from tags after writing")
    ap.add_argument("--no-strip-diacritics", action="store_true", help="Keep diacritics in final tag values")
    ap.add_argument("--prefer-id3v1", action="store_true", help="Prefer ID3v1 when v1/v2 differ (default: prefer v2)")
    # Album-level overrides
    ap.add_argument("--set-author", dest="set_author", default=None)
    ap.add_argument("--set-year", dest="set_year", default=None)
    ap.add_argument("--set-album", dest="set_album", default=None)
    ap.add_argument("--set-performer", dest="set_performer", default=None)
    ap.add_argument("--set-translator", dest="set_translator", default=None)
    ap.add_argument("--genre", dest="set_genre", default=None)
    # Bulk replacements
    ap.add_argument("--replace", action="append", metavar="FROM=>TO",
                    help="Replace text in all tags. Repeatable.")
    ap.add_argument("--replace-file", help="JSON file with list of replacement rules.")
    ap.add_argument("--strip-diacritics-all", action="store_true",
                    help="Strip diacritics from ALL text tags at write time.")
    ap.add_argument("--tracks-only", action="store_true",
                    help="Skip album prompts; reuse last accepted album values (or AUTO if none). Review tracks only.")
    args = ap.parse_args()

    # config toggles
    CONFIG["skip_intro_00_when_renumber"] = not args.no_skip_intro
    CONFIG["strip_diacritics_in_tags"] = not args.no_strip_diacritics
    CONFIG["prefer_id3v2"] = not args.prefer_id3v1

    # overrides (album-level)
    cli_overrides = {
        "Artist": args.set_author,
        "AlbumArtist": args.set_author,
        "Album": args.set_album,
        "Performer": args.set_performer,
        "Translator": args.set_translator,
        "Genre": args.set_genre,
        "Date": args.set_year,
    }
    # keep only provided
    cli_overrides = {k: v for k, v in cli_overrides.items() if v}

    # merge with overrides JSON from downloader (if any future integration passes it)
    file_overrides: Dict[str, str] = {}

    # replacement rules: CONFIG + file + CLI
    cli_rules: List[Dict[str, Any]] = []
    for r in (args.replace or []):
        if "=>" not in r:
            print(cred(f"--replace must look like 'FROM=>TO', got: {r}"))
            continue
        a, b = r.split("=>", 1)
        cli_rules.append({"pattern": a, "replacement": b, "regex": False, "fields": []})
    file_rules = _load_replace_file(args.replace_file) if args.replace_file else []
    replace_rules = (CONFIG.get("bulk_replacements") or []) + file_rules + cli_rules

    overrides = {**file_overrides, **cli_overrides}

    # Non-interactive path
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
            overrides=overrides,
            replace_rules=replace_rules,
            strip_all_text_tags=args.strip_diacritics_all,
        )
        print(msg)
        return 0 if ok else 1

    # Interactive flow
    print(f"Scanning folder: {args.folder}")
    exif_data = run_exiftool(args.folder)
    if not exif_data:
        print(cred("No supported audio metadata found or exiftool missing."), file=sys.stderr)
        return 2

    album = suggest_album_level(args.folder, exif_data, overrides=overrides)
    album_user = {}
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
    legacy = collect_legacy_text(args.folder)
    build_nfo(args.folder, suggestions, exif_data, legacy)

    # Review (ENTER=accept)
    summarize_missing_and_suggested(album["auto"], exif_data, args.folder)
    album_final = review_album_level(album["auto"], {})
    track_final = review_tracks(track_diffs)

    plan, csv_rows = build_apply_plan(
        args.folder, exif_data, album_final, track_final,
        args.renumber, CONFIG["skip_intro_00_when_renumber"], args.rename,
        replace_rules=[_compile_rule(r) for r in replace_rules],
        force_strip_all=args.strip_diacritics_all
    )
    save_json(args.folder, suggestions)
    save_csv(args.folder, csv_rows)

    from_choice = input(cyellow("\nApply all changes to files? [Y/n]: ")).strip().lower()
    if from_choice in ("", "y", "yes"):
        failures = 0
        for item in plan:
            ok, msg = mutagen_write(item["file"], item["tags"], item.get("embed_cover"))
            if not ok:
                print(cred(f"Write failed: {item['file']} → {msg}"))
                failures += 1
                continue
            nf = item.get("new_filename")
            if nf:
                old = item["file"]
                new = os.path.join(os.path.dirname(old), nf)
                if os.path.abspath(old) != os.path.abspath(new):
                    try:
                        shutil.move(old, new)
                    except Exception as e:
                        print(cred(f"Rename failed: {old} → {new}: {e}"))
                        failures += 1
        if failures:
            print(cred(f"Done with {failures} failures."))
        else:
            print(cgreen("All changes applied successfully."))
    else:
        print(cgray("No changes applied. Suggestions, CSV, and NFO saved."))
    return 0

if __name__ == "__main__":
    sys.exit(main())