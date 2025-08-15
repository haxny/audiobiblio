#!/usr/bin/env python3
"""
tags_analyze_folder.py
Updated: robust exiftool handling + safe rerun + colorized output.

Usage:
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

def run_exiftool(folder, audio_exts=("mp3","m4b","flac")):
    """
    Run exiftool constrained to audio extensions.
    If the first non-recursive run returns no stdout, retry recursively (-r).
    Returns a list (possibly empty) of parsed exiftool JSON objects.
    Prints helpful stderr output if exiftool fails to return JSON.
    """
    base_cmd = ["exiftool", "-j", "-G1", "-charset", "utf8"]
    for ext in audio_exts:
        base_cmd += ["-ext", ext]
    base_cmd.append(str(folder))

    # Run non-recursive first
    try:
        proc = subprocess.run(base_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        print(f"{C.RED}Error:{C.RESET} exiftool not found. Install exiftool and ensure it is in your PATH.")
        sys.exit(1)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # If no stdout, show stderr and retry with recursion
    if not stdout.strip():
        if stderr.strip():
            print(f"{C.YELLOW}ExifTool stderr (non-recursive):{C.RESET}\n{stderr.strip()}\n")
        # Retry with recursion - maybe files are inside subfolders
        print(f"{C.YELLOW}No JSON output from non-recursive exiftool. Retrying with recursive scan (-r)...{C.RESET}")
        recursive_cmd = base_cmd[:-1] + ["-r", str(folder)]
        proc2 = subprocess.run(recursive_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout2 = proc2.stdout or ""
        stderr2 = proc2.stderr or ""
        if not stdout2.strip():
            print(f"{C.RED}ExifTool produced no JSON output even when recursing.{C.RESET}")
            if stderr2.strip():
                print(f"{C.RED}ExifTool stderr (recursive):{C.RESET}\n{stderr2.strip()}")
            print(f"{C.RED}No audio files matched or exiftool failed. Exiting.{C.RESET}")
            return []
        try:
            data = json.loads(stdout2)
            # filter by FileType anyway
            audio_lower = {e.lower() for e in audio_exts}
            return [entry for entry in data if entry.get("File:FileType","").lower() in audio_lower or entry.get("FileType","").lower() in audio_lower]
        except json.JSONDecodeError:
            print(f"{C.RED}Failed to parse JSON returned by exiftool (recursive).{C.RESET}")
            print(f"{C.RED}Raw output (first 1000 chars):{C.RESET}\n{stdout2[:1000]}")
            return []
    else:
        # parse stdout of first run
        try:
            data = json.loads(stdout)
            audio_lower = {e.lower() for e in audio_exts}
            return [entry for entry in data if entry.get("File:FileType","").lower() in audio_lower or entry.get("FileType","").lower() in audio_lower]
        except json.JSONDecodeError:
            # Try to show stderr and a snippet for diagnostics
            print(f"{C.RED}Failed to parse JSON from exiftool (non-recursive).{C.RESET}")
            if stderr.strip():
                print(f"{C.YELLOW}ExifTool stderr:{C.RESET}\n{stderr.strip()}\n")
            print(f"{C.RED}Raw output (first 1000 chars):{C.RESET}\n{stdout[:1000]}")
            # fallback retry recursive
            print(f"{C.YELLOW}Retrying with recursive scan (-r) as a fallback...{C.RESET}")
            recursive_cmd = base_cmd[:-1] + ["-r", str(folder)]
            proc2 = subprocess.run(recursive_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout2 = proc2.stdout or ""
            stderr2 = proc2.stderr or ""
            if not stdout2.strip():
                print(f"{C.RED}Recursive retry also produced no JSON. Exiting.{C.RESET}")
                if stderr2.strip():
                    print(f"{C.RED}ExifTool stderr (recursive):{C.RESET}\n{stderr2.strip()}")
                return []
            try:
                data = json.loads(stdout2)
                return [entry for entry in data if entry.get("File:FileType","").lower() in audio_lower or entry.get("FileType","").lower() in audio_lower]
            except json.JSONDecodeError:
                print(f"{C.RED}Failed to parse JSON on recursive retry. Exiting.{C.RESET}")
                print(f"{C.RED}Raw output (first 1000 chars):{C.RESET}\n{stdout2[:1000]}")
                return []

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
    print(f"{color}{code:<6} {tag_col} {orig_col} {arrow} {final_col}{extra}{C.RESET}")

def load_prev_suggestions(folder):
    suggestions_path = os.path.join(folder, "_tags_suggestions.json")
    prev = {}
    prev_codes = {}
    if os.path.exists(suggestions_path):
        try:
            with open(suggestions_path, "r", encoding="utf-8") as fh:
                prev = json.load(fh)
            # collect previous codes (file_key, tag) -> code
            for fkey, tags in prev.items():
                if not isinstance(tags, dict):
                    continue
                for tkey, tdata in tags.items():
                    if isinstance(tdata, dict):
                        code = tdata.get("edit_code")
                        if code:
                            prev_codes[(fkey, tkey)] = code
        except Exception:
            # if parsing fails, ignore previous suggestions (force regen behavior handled by caller)
            prev = {}
            prev_codes = {}
    return prev, prev_codes

def next_code_number(prev_codes):
    nums = []
    for v in prev_codes.values():
        if isinstance(v, str) and len(v) > 1:
            suffix = v[1:]
            if suffix.isdigit():
                nums.append(int(suffix))
    return max(nums) + 1 if nums else 1

def main(folder, verbose=False, force=False):
    folder = os.path.abspath(folder)
    print(f"{C.CYAN}Scanning folder:{C.RESET} {folder}")
    prev_suggestions, prev_codes = ({}, {}) if force else load_prev_suggestions(folder)

    meta = run_exiftool(folder)
    if meta is None:
        print(f"{C.RED}Exiting due to exiftool issues.{C.RESET}")
        sys.exit(1)
    if len(meta) == 0:
        print(f"{C.RED}No audio files found or exiftool returned no usable entries in folder.{C.RESET}")
        sys.exit(0)

    suggestions = {}
    diacritic_archive = {}
    track_count = len(meta)
    pad_len = 3 if track_count >= 100 else 2

    code_counter = next_code_number(prev_codes)

    # Print legend and album header
    print(f"{C.GREEN}Legend:{C.RESET} {C.DIM}prev{C.RESET} {C.GRAY}unchanged{C.RESET} {C.YELLOW}autosuggest{C.RESET} {C.MAGENTA}encfix{C.RESET} {C.RED}missing{C.RESET}")

    # We'll gather album-level tags by frequency across files and present them first
    # collect tag frequency
    tag_freq = {}
    file_map = {}
    for entry in meta:
        fname = entry.get("File:FileName") or os.path.basename(entry.get("SourceFile","")) or ""
        file_map[fname] = entry
        for key, val in entry.items():
            if not isinstance(val, str):
                continue
            tag_name = key.split(":")[-1]
            tag_freq.setdefault(tag_name, []).append(val)

    # Decide album-level tags as tags that appear in many files (simple heuristic)
    album_level_candidates = [k for k,v in tag_freq.items() if len(v) >= max(1, track_count//2)]

    # Print album-level section
    print(f"\n{C.CYAN}=== ALBUM-LEVEL TAGS (inferred) ==={C.RESET}")
    album_codes = {}
    for tag in sorted(album_level_candidates):
        # pick the most common value
        vals = tag_freq.get(tag,[])
        if not vals:
            continue
        from collections import Counter
        common = Counter(vals).most_common(1)[0][0]
        enc_suspect, repaired, source = detect_encoding_issue(common, {"filename": ""})
        final_val = strip_diacritics(repaired)
        # code assignment: reuse existing if any (look up any file that had this tag)
        found_code = None
        for fname, entry in file_map.items():
            if tag in entry:
                prev_code = prev_codes.get((fname, tag))
                if prev_code:
                    found_code = prev_code
                    break
        if not found_code:
            found_code = f"a{code_counter:02d}"
            code_counter += 1
        album_codes[tag] = found_code
        # prev check
        prev_final = None
        for fname in file_map:
            prev_final = prev_suggestions.get(fname, {}).get(tag, {}).get("final")
            if prev_final:
                break
        status = "unchanged"
        if prev_final:
            if prev_final == final_val:
                status = "prev"
            else:
                status = "autosuggest"
        elif enc_suspect:
            status = "encfix"
        # print only when meaningful or verbose
        if verbose or status in ("autosuggest","encfix","missing"):
            print_tag_line(found_code, tag, common, final_val, status, source)
        # store in suggestions root under a pseudo-file key "_ALBUM"
        suggestions.setdefault("_ALBUM", {})[tag] = {
            "original": common,
            "repaired": repaired,
            "final": prev_final if prev_final else final_val,
            "encoding_suspect": enc_suspect,
            "repair_source": source,
            "edit_code": found_code
        }

    # Now per-file / per-track details
    print(f"\n{C.CYAN}=== TRACKS ==={C.RESET}")
    idx = 0
    for fname, entry in sorted(file_map.items()):
        idx += 1
        print(f"\n{C.CYAN}--- {fname} ({idx}/{track_count}) ---{C.RESET}")
        suggestions[fname] = {}
        diacritic_archive[fname] = {}
        for key, val in entry.items():
            if not isinstance(val, str):
                continue
            tag_name = key.split(":")[-1]
            # comparisons: filename, album-level common value, no playlist for now
            comparisons = {"filename": os.path.splitext(fname)[0]}
            if tag_name in suggestions.get("_ALBUM", {}):
                comparisons["album"] = suggestions["_ALBUM"][tag_name]["original"]

            enc_suspect, repaired, source = detect_encoding_issue(val, comparisons)
            final_val = strip_diacritics(repaired)
            # reuse previous code if exists
            edit_code = prev_codes.get((fname, tag_name))
            if not edit_code:
                edit_code = f"t{code_counter:02d}"
                code_counter += 1

            prev_final = prev_suggestions.get(fname, {}).get(tag_name, {}).get("final")

            if prev_final:
                if prev_final == final_val:
                    status = "prev"
                else:
                    status = "autosuggest"
            elif not val.strip():
                status = "missing"
            elif enc_suspect:
                status = "encfix"
            elif repaired != val:
                status = "autosuggest"
            else:
                status = "unchanged"

            # store
            suggestions[fname][tag_name] = {
                "original": val,
                "repaired": repaired,
                "final": prev_final if prev_final is not None else final_val,
                "encoding_suspect": enc_suspect,
                "repair_source": source,
                "edit_code": edit_code
            }
            diacritic_archive[fname][tag_name] = repaired

            show_this = verbose or status in ("missing", "autosuggest", "encfix")
            if show_this:
                print_tag_line(edit_code, tag_name, val, final_val, status, source)

    # Add scan metadata and save
    suggestions["_scan_metadata"] = {
        "scan_date": datetime.now().isoformat(),
        "script_version": "1.2",
        "safe_rerun": not force
    }
    with open(os.path.join(folder, "_tags_suggestions.json"), "w", encoding="utf-8") as out:
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
