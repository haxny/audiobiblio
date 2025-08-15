#!/usr/bin/env python3
"""
tags_analyze_folder.py
----------------------
Phase 1 of audiobook metadata cleanup toolkit.

Features:
---------
- Scan audiobook folders with exiftool (only audio files).
- Detect encoding issues, diacritics removal, and missing tags.
- Preserve user edits between runs (safe rerun mode).
- Column-aligned, color-coded terminal output for easy reading.
- Separate album-level vs. per-track display.
- Prompts for missing Album, Genre, Date values.
- JSON outputs: final suggestions & archival repaired diacritics version.
- Verbose mode to see all tags; default shows only changed/missing.

Usage:
------
    python3 tags_analyze_folder.py /path/to/folder
    python3 tags_analyze_folder.py /path/to/folder --verbose
    python3 tags_analyze_folder.py /path/to/folder --force
"""

import os
import sys
import json
import subprocess
import unicodedata
from datetime import datetime
import argparse

# ====== CONFIG ======
VALID_DIACRITICS = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
INTRO_TRACK_NAMES = ["uvod", "intro", "předmluva", "predmluva"]
SHOW_TAGS_FIRST = ["Album", "Artist", "AlbumArtist", "Performer", "Genre", "Date"]

# ====== COLORS ======
class C:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    GRAY = "\033[90m"
    DIM = "\033[2m"
    CYAN = "\033[96m"

def strip_diacritics(text):
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])

def count_valid_diacritics(text):
    return sum(1 for c in text if c in VALID_DIACRITICS)

def try_encoding_repairs(text):
    repairs = {}
    for src_enc, tgt_enc in [("latin1", "cp1250"), ("latin1", "iso-8859-2")]:
        try:
            repaired = text.encode(src_enc).decode(tgt_enc)
            repairs[f"{src_enc}->{tgt_enc}"] = repaired
        except Exception:
            continue
    return repairs

def run_exiftool(folder):
    audio_exts = ["mp3", "m4b", "flac"]
    cmd = ["exiftool", "-j", "-G1", "-charset", "utf8"]
    for ext in audio_exts:
        cmd += ["-ext", ext]
    cmd.append(folder)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        print(f"{C.RED}ExifTool error:{C.RESET}", res.stderr.decode())
        sys.exit(1)
    data = json.loads(res.stdout.decode("utf-8"))
    return [entry for entry in data if entry.get("File:FileType", "").lower() in audio_exts]

def detect_encoding_issue(value, comparisons):
    if not value or not isinstance(value, str):
        return False, value, None
    if count_valid_diacritics(value) > 0:
        return False, value, None
    for src, comp in comparisons.items():
        if comp and count_valid_diacritics(comp) > 0:
            repairs = try_encoding_repairs(value)
            best_fix, best_score = None, 0
            for k, rep in repairs.items():
                score = count_valid_diacritics(rep)
                if score > best_score:
                    best_fix, best_score = rep, score
            if best_fix and best_score > 0:
                return True, best_fix, src
    return False, value, None

def print_tag_line(code, tag, original, final, status, source=None):
    tag_col = f"{tag:<15}"
    orig_col = f"{(original or ''):<40.40}"
    arrow = "→"
    final_col = f"{final or ''}"
    if status == "prev":
        color = C.DIM
    elif status == "unchanged":
        color = C.GRAY
    elif status == "userchange":
        color = C.GREEN
    elif status == "autosuggest":
        color = C.YELLOW
    elif status == "encfix":
        color = C.MAGENTA
    elif status == "missing":
        color = C.RED
    else:
        color = C.RESET
    extra = f" [ENC FIX from {source}]" if status == "encfix" and source else ""
    print(f"{color}{code:<5} {tag_col} {orig_col} {arrow} {final_col}{extra}{C.RESET}")

def main(folder, verbose=False, force=False):
    print(f"{C.CYAN}Scanning folder:{C.RESET} {folder}")
    prev_suggestions = {}
    prev_codes = {}
    suggestions_path = os.path.join(folder, "_tags_suggestions.json")
    if os.path.exists(suggestions_path) and not force:
        with open(suggestions_path, "r", encoding="utf-8") as f:
            prev_suggestions = json.load(f)
        for fkey, tags in prev_suggestions.items():
            if isinstance(tags, dict):
                for tkey, tdata in tags.items():
                    prev_codes[(fkey, tkey)] = tdata.get("edit_code")

    meta = run_exiftool(folder)
    suggestions = {}
    diacritic_archive = {}
    track_count = sum(1 for m in meta)
    pad_len = 3 if track_count >= 100 else 2

    code_counter = max([int(c[1:]) for c in prev_codes.values()] + [0]) + 1

    # Album-level first
    print(f"\n{C.CYAN}=== ALBUM LEVEL TAGS ==={C.RESET}")
    for entry in meta:
        fname = entry.get("File:FileName", "")
        file_key = fname
        suggestions[file_key] = {}
        diacritic_archive[file_key] = {}
        track_tag_seen = False

        for tag, val in entry.items():
            if not isinstance(val, str):
                continue
            tag_clean = tag.split(":")[-1]

            enc_suspect, repaired, source = detect_encoding_issue(val, {"filename": fname})
            final_val = strip_diacritics(repaired)
            diacritic_archive[file_key][tag_clean] = repaired

            edit_code = prev_codes.get((file_key, tag_clean))
            if not edit_code:
                edit_code = f"a{code_counter:02d}" if tag_clean in SHOW_TAGS_FIRST else f"t{code_counter:02d}"
                code_counter += 1

            prev_entry = prev_suggestions.get(file_key, {}).get(tag_clean)
            if prev_entry:
                if prev_entry["final"] == final_val:
                    status = "prev"
                elif prev_entry["final"] != final_val:
                    status = "autosuggest"
            elif not val.strip():
                status = "missing"
            elif enc_suspect:
                status = "encfix"
            elif repaired != val:
                status = "autosuggest"
            else:
                status = "unchanged"

            suggestions[file_key][tag_clean] = {
                "original": val,
                "repaired": repaired,
                "final": prev_entry["final"] if prev_entry else final_val,
                "encoding_suspect": enc_suspect,
                "repair_source": source,
                "edit_code": edit_code
            }

            show_this = verbose or status in ("missing", "autosuggest", "encfix")
            if show_this:
                if tag_clean in SHOW_TAGS_FIRST and not track_tag_seen:
                    print(f"\n{C.CYAN}=== TRACK {fname} ==={C.RESET}")
                    track_tag_seen = True
                print_tag_line(edit_code, tag_clean, val, final_val, status, source)

    suggestions["_scan_metadata"] = {
        "scan_date": datetime.now().isoformat(),
        "script_version": "1.1",
        "safe_rerun": not force
    }
    with open(suggestions_path, "w", encoding="utf-8") as out:
        json.dump(suggestions, out, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "_tags_with_diacritics.json"), "w", encoding="utf-8") as out:
        json.dump(diacritic_archive, out, indent=2, ensure_ascii=False)
    print(f"\n{C.CYAN}Saved:{C.RESET} _tags_suggestions.json and _tags_with_diacritics.json")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="Folder to scan")
    parser.add_argument("--verbose", action="store_true", help="Show all tags")
    parser.add_argument("--force", action="store_true", help="Overwrite previous suggestions")
    args = parser.parse_args()
    main(args.folder, verbose=args.verbose, force=args.force)
