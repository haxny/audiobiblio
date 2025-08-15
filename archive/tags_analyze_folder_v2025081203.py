#!/usr/bin/env python3
"""
tags_analyze_folder.py
----------------------
Phase 1 of audiobook metadata cleanup toolkit.

Purpose:
--------
- Scan all audio files in a folder.
- Compare metadata from exiftool with filenames, playlists, and JSON sidecars.
- Detect possible mojibake / encoding errors before removing diacritics.
- Produce both:
    - Final diacritics-free tag suggestions for your library.
    - Archival repaired version with correct diacritics.
- Assign edit codes (aXX for album-level, tXX for track-level).
- Prompt for missing dates if audiobook genre.
- Output full color-coded report for manual inspection.

Future Extensibility:
---------------------
- Cross-folder tag comparison.
- Series detection from folder structure.
- External metadata lookups (databazeknih.cz, cbdb.cz, etc.).
- Auto cover art fetching.

Requirements:
-------------
- Python 3.8+
- exiftool installed and in PATH
"""

import os
import sys
import json
import subprocess
import unicodedata

# ====== CONFIG ======
VALID_DIACRITICS = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
INTRO_TRACK_NAMES = ["uvod", "intro", "předmluva", "predmluva"]

# ====== COLORS ======
class C:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"

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

def read_playlists(folder):
    data = []
    for f in os.listdir(folder):
        if f.lower().endswith((".m3u", ".m3u8")):
            with open(os.path.join(folder, f), "r", errors="ignore") as fh:
                data.extend(fh.read().splitlines())
    return data

def read_sidecar_json(folder):
    sidecars = []
    for f in os.listdir(folder):
        if f.lower().endswith(".json"):
            try:
                with open(os.path.join(folder, f), "r", encoding="utf-8") as fh:
                    sidecars.append(json.load(fh))
            except Exception:
                pass
    return sidecars

def detect_encoding_issue(value, comparisons):
    """
    Returns (encoding_suspect, repair_value, repair_source)
    """
    if not value or not isinstance(value, str):
        return False, value, None
    if count_valid_diacritics(value) > 0:
        return False, value, None  # already fine
    # Compare with other sources
    for src, comp in comparisons.items():
        if comp and count_valid_diacritics(comp) > 0:
            repairs = try_encoding_repairs(value)
            best_fix = None
            best_score = 0
            for k, rep in repairs.items():
                score = count_valid_diacritics(rep)
                if score > best_score:
                    best_fix = rep
                    best_score = score
            if best_fix and best_score > 0:
                return True, best_fix, src
    return False, value, None

def main(folder):
    print(f"{C.YELLOW}Scanning folder:{C.RESET} {folder}")
    meta = run_exiftool(folder)
    playlists = read_playlists(folder)
    sidecars = read_sidecar_json(folder)

    suggestions = {}
    diacritic_archive = {}

    track_count = sum(1 for m in meta if m.get("File:FileType", "").lower() in ["mp3", "m4b", "flac"])
    pad_len = 3 if track_count >= 100 else 2

    code_counter = 1
    print(f"{C.GREEN}Legend:{C.RESET} {C.GREEN}✓ unchanged{C.RESET}, {C.YELLOW}Δ changed{C.RESET}, {C.MAGENTA}ENC FIX{C.RESET}, {C.RED}⚠ missing{C.RESET}")

    for entry in meta:
        fname = entry.get("File:FileName", "")
        file_key = fname
        suggestions[file_key] = {}
        diacritic_archive[file_key] = {}

        for tag, val in entry.items():
            if not isinstance(val, str):
                continue

            # Comparisons from filename, playlist, json sidecars
            comp_sources = {}
            comp_sources["filename"] = os.path.splitext(fname)[0]
            for pl in playlists:
                if fname in pl:
                    comp_sources["playlist"] = pl
            for sc in sidecars:
                if isinstance(sc, dict):
                    for k, v in sc.items():
                        if isinstance(v, str) and os.path.splitext(fname)[0] in v:
                            comp_sources["json"] = v

            enc_suspect, repaired, source = detect_encoding_issue(val, comp_sources)

            final_val = strip_diacritics(repaired)
            diacritic_archive[file_key][tag] = repaired
            suggestions[file_key][tag] = {
                "original": val,
                "repaired": repaired,
                "final": final_val,
                "encoding_suspect": enc_suspect,
                "repair_source": source,
                "edit_code": f"a{code_counter:02d}"
            }

            # Missing date check for audiobooks
            if tag.lower() in ["id3:genre", "quicktime:genre", "vorbis:genre"]:
                genre_val = final_val.lower()
                if any(kw in genre_val for kw in ["audiokniha", "audiobook"]):
                    # Look for Date/Year tags in this entry
                    if not any(k.lower().endswith("date") or k.lower().endswith("year") for k in entry.keys()):
                        user_date = input(f"{C.RED}Date missing for audiobook \"{fname}\". Enter year of issue (YYYY): {C.RESET}").strip()
                        if user_date:
                            suggestions[file_key]["Date"] = {
                                "original": "",
                                "repaired": user_date,
                                "final": user_date,
                                "encoding_suspect": False,
                                "repair_source": "manual",
                                "edit_code": f"a{code_counter:02d}"
                            }

            # Terminal output
            if not val.strip():
                print(f"{C.RED}{suggestions[file_key][tag]['edit_code']}: {tag} MISSING{C.RESET}")
            elif enc_suspect:
                print(f"{C.MAGENTA}{suggestions[file_key][tag]['edit_code']}: [ENC FIX from {source}] Original=\"{val}\" → Repaired=\"{repaired}\" → Final=\"{final_val}\"{C.RESET}")
            elif repaired != val:
                print(f"{C.YELLOW}{suggestions[file_key][tag]['edit_code']}: {tag} Original=\"{val}\" → Final=\"{final_val}\"{C.RESET}")
            else:
                print(f"{C.GREEN}{suggestions[file_key][tag]['edit_code']}: {tag} Original=\"{val}\" → Final=\"{final_val}\"{C.RESET}")

            code_counter += 1

    # Save outputs
    with open(os.path.join(folder, "_tags_suggestions.json"), "w", encoding="utf-8") as out:
        json.dump(suggestions, out, indent=2, ensure_ascii=False)
    with open(os.path.join(folder, "_tags_with_diacritics.json"), "w", encoding="utf-8") as out:
        json.dump(diacritic_archive, out, indent=2, ensure_ascii=False)

    print(f"{C.YELLOW}Saved suggestions to _tags_suggestions.json and _tags_with_diacritics.json{C.RESET}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 tags_analyze_folder.py /path/to/folder")
        sys.exit(1)
    main(sys.argv[1])
