#!/usr/bin/env python3
"""
tags_analyze_folder.py

Scan a single audiobook folder (uses _tags_backup.json if present, otherwise runs exiftool),
build suggested normalized tags (no diacritics, UTF-8), attempt to extract narrator from Comment,
infer track order from .m3u or filename, and write a suggestions JSON for your review.

This script DOES NOT write metadata to files. It's the preview step.
"""
import sys
import json
import subprocess
from pathlib import Path
import unicodedata
import re
from collections import Counter, defaultdict

AUDIO_EXTS = {".mp3", ".m4b", ".flac", ".aac", ".m4a"}

def remove_diacritics(s):
    if not isinstance(s, str):
        s = str(s)
    return ''.join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def titlecase_name(s):
    s2 = remove_diacritics(s).strip()
    # basic titlecase; user can edit later if needed
    return " ".join(word.capitalize() for word in re.split(r'\s+', s2))

def run_exiftool(folder):
    print("Running exiftool to build a fresh backup (this may take a moment)...")
    cmd = ["exiftool", "-j", "-G1", "-charset", "utf8", "-r", str(folder)]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0 and not res.stdout:
        print("ExifTool error:", res.stderr.strip())
        raise SystemExit(1)
    data = json.loads(res.stdout)
    return data

def load_backup_or_run(folder):
    backup = folder / "_tags_backup.json"
    if backup.exists():
        print("Loading existing backup:", backup)
        with open(backup, "r", encoding="utf-8") as f:
            return json.load(f), backup
    data = run_exiftool(folder)
    # save a backup for later review
    with open(folder / "_tags_backup.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Saved new backup to _tags_backup.json")
    return data, folder / "_tags_backup.json"

def find_m3u_order(folder):
    for p in folder.iterdir():
        if p.suffix.lower() == ".m3u":
            print("Found playlist:", p.name)
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            order = []
            for L in lines:
                L = L.strip()
                if not L or L.startswith("#"):
                    continue
                # Convert to a basename for matching
                order.append(Path(L).name)
            return order, p
    return None, None

def extract_narrator_from_comment(comment):
    if not comment:
        return ""
    raw = str(comment)
    # remove diacritics to make patterns stable
    nd = remove_diacritics(raw).lower()
    # common patterns: "cte:", "cte ", "cte by", "read by"
    # find last colon (works for "Čte: Name")
    if ":" in raw:
        after = raw.split(":", 1)[1].strip()
        return titlecase_name(after)
    # fallback search for 'cte' or 'read by' etc
    if "cte" in nd:
        m = re.search(r'(?i)cte[:\s-]*([\w\W]+)$', nd)
        if m:
            return titlecase_name(m.group(1))
    m = re.search(r'(?i)read by[:\s-]*([\w\W]+)$', nd)
    if m:
        return titlecase_name(m.group(1))
    # otherwise try last two words of comment
    parts = raw.split()
    if len(parts) >= 2:
        return titlecase_name(" ".join(parts[-2:]))
    return ""

def infer_language_from_comment(comment):
    if not comment:
        return None
    nd = remove_diacritics(str(comment)).lower()
    if "cte" in nd or "uvod" in nd or "autor" in nd:
        return "cz"
    # naive check for Slovak (could be improved later)
    if "citaj" in nd or "preklad" in nd:
        return "sk"
    # default None => ask user later
    return None

def numeric_prefix(filename):
    m = re.match(r'^\s*0*([0-9]+)', filename)
    if m:
        return int(m.group(1))
    return None

def suggested_title_from_filename(fname):
    # remove extension, leading num and separators
    stem = Path(fname).stem
    s = re.sub(r'^\s*\d+\s*[-_. ]*\s*', '', stem)
    return remove_diacritics(s).strip().replace('_', ' ')

def common_value(counter):
    if not counter:
        return ""
    val, cnt = counter.most_common(1)[0]
    return val

def build_suggestions(all_data, folder, m3u_order):
    # Map basename -> record
    records = { Path(d.get("SourceFile","")).name: d for d in all_data }
    basenames = sorted(records.keys())

    # If m3u provided, build order from it; else try numeric prefix or filename sort
    order = []
    if m3u_order:
        for name in m3u_order:
            if name in records:
                order.append(name)
        # append any missing files at end
        for b in basenames:
            if b not in order:
                order.append(b)
    else:
        # try numeric prefix
        numbered = []
        others = []
        for b in basenames:
            n = numeric_prefix(b)
            if n is not None:
                numbered.append((n, b))
            else:
                others.append(b)
        numbered.sort()
        order = [b for (_, b) in numbered] + sorted(others)

    # infer common artist (author)
    artists = Counter()
    for r in records.values():
        a = r.get("Artist") or r.get("ARTIST") or ""
        if a:
            artists[a] += 1
    raw_common_artist = common_value(artists)
    suggested_author = titlecase_name(raw_common_artist) if raw_common_artist else None

    # overall language detection from comments
    lang_votes = Counter()
    for r in records.values():
        c = r.get("Comment") or r.get("COMMENT") or ""
        lang = infer_language_from_comment(c)
        if lang:
            lang_votes[lang] += 1
    inferred_lang = None
    if lang_votes:
        inferred_lang = lang_votes.most_common(1)[0][0]

    genre_map = {"cz": "audiokniha", "sk": "audiokniha (SK)", "en": "audiobook"}
    suggested_genre = genre_map.get(inferred_lang, "audiokniha")  # default to Czech if ambiguous

    # Build per-file suggestions
    suggestions = []
    for idx, basename in enumerate(order, start=1):
        r = records[basename]
        cur_title = r.get("Title") or r.get("TITLE") or ""
        cur_artist = r.get("Artist") or ""
        cur_albumartist = r.get("AlbumArtist") or r.get("Albumartist") or ""
        cur_performer = r.get("Performer") or ""
        cur_genre = r.get("Genre") or ""
        cur_track = r.get("Track") or r.get("TrackNumber") or ""
        cur_date = r.get("Date") or r.get("Year") or ""

        # suggested values:
        s_title = suggested_title_from_filename(basename)
        s_artist = suggested_author or titlecase_name(cur_artist) if cur_artist else suggested_author or ""
        s_albumartist = s_artist
        # narrator extraction from comment
        s_performer = extract_narrator_from_comment(r.get("Comment") or "")
        s_genre = suggested_genre if suggested_genre else (remove_diacritics(cur_genre) if cur_genre else "")
        # Date: prefer Year if valid numeric non-zero, else Date
        s_date = ""
        try:
            y = int(r.get("Year") or 0)
            if y > 0:
                s_date = str(y)
            else:
                # try 'CreateDate' or 'DateTimeOriginal'
                if r.get("CreateDate"):
                    s_date = str(r.get("CreateDate"))
                elif r.get("DateTimeOriginal"):
                    s_date = str(r.get("DateTimeOriginal"))
        except Exception:
            s_date = r.get("Date") or ""

        # track: use idx (1-based) and zero-pad to two digits for display (you can change)
        s_track = str(idx)

        # translator: none for now (will look for TXXX:Translator or other fields)
        s_translator = r.get("TXXX:Translator") or r.get("Translator") or ""

        suggestions.append({
            "SourceFile": r.get("SourceFile"),
            "FileName": basename,
            "Current": {
                "Title": cur_title,
                "Artist": cur_artist,
                "AlbumArtist": cur_albumartist,
                "Performer": cur_performer,
                "Genre": cur_genre,
                "Track": cur_track,
                "Date": cur_date,
                "Comment": r.get("Comment","")
            },
            "Suggested": {
                "Title": remove_diacritics(s_title),
                "Artist": remove_diacritics(s_artist) if s_artist else "",
                "AlbumArtist": remove_diacritics(s_albumartist) if s_albumartist else "",
                "Performer": remove_diacritics(s_performer) if s_performer else "",
                "Genre": remove_diacritics(s_genre) if s_genre else "",
                "Track": s_track,
                "Date": s_date,
                "Translator": remove_diacritics(s_translator) if s_translator else ""
            }
        })
    # high-level suggestions
    folder_suggestion = {
        "Folder": str(folder),
        "SuggestedAuthor": suggested_author,
        "SuggestedGenre": suggested_genre,
        "InferredLanguage": inferred_lang,
        "OrderSource": "m3u" if m3u_order else "filename",
        "Files": suggestions
    }
    return folder_suggestion

def pretty_print_summary(sugg):
    print("\n=== Folder summary suggestion ===")
    print("Folder:", sugg["Folder"])
    print("Suggested author (albumartist/artist):", sugg["SuggestedAuthor"])
    print("Suggested genre:", sugg["SuggestedGenre"], "(inferred language:", sugg["InferredLanguage"], ")")
    print("Track order determined from:", sugg["OrderSource"])
    print("\nFiles (current vs suggested):")
    for f in sugg["Files"]:
        cur = f["Current"]
        s = f["Suggested"]
        diffs = []
        for k in ["Title","Artist","AlbumArtist","Performer","Genre","Track","Date","Translator"]:
            if str(cur.get(k,"")).strip() != str(s.get(k,"")).strip():
                diffs.append(k)
        flags = ", ".join(diffs) if diffs else "no-change"
        print(f" - {f['FileName']}: changes -> {flags}")
        # show small table for quick review
        print(f"    Current Title:  {cur.get('Title','')}")
        print(f"    Suggested Title:{s.get('Title','')}")
        print(f"    Current Artist: {cur.get('Artist','')}")
        print(f"    Suggested Artist:{s.get('Artist','')}")
        print(f"    Current Performer:{cur.get('Performer','')}")
        print(f"    Suggested Performer:{s.get('Performer','')}")
        print(f"    Current Track:  {cur.get('Track','')}")
        print(f"    Suggested Track: {s.get('Track','')}")
        print("")

def save_suggestions(folder, sugg):
    out = Path(folder) / "_tags_suggestions.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(sugg, f, ensure_ascii=False, indent=2)
    print("Saved suggestions to", out)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_folder.py /path/to/folder")
        sys.exit(1)
    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print("Not a folder:", folder)
        sys.exit(1)

    all_data, backup_path = load_backup_or_run(folder)
    m3u_order, m3u_path = find_m3u_order(folder)
    suggestion = build_suggestions(all_data, folder, m3u_order)
    pretty_print_summary(suggestion)
    save_suggestions(folder, suggestion)

    print("\nNEXT STEPS:")
    print(" - Inspect _tags_suggestions.json. If it looks OK, we'll implement the interactive apply step.")
    print(" - No files were changed by this script.")
    print(" - If you want, paste the suggestions.json here (or its summary) and we will proceed to an interactive apply step.")

if __name__ == "__main__":
    main()
