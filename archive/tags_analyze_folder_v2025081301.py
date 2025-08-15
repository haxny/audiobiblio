#!/usr/bin/env python3
import os
import sys
import json
import csv
import re
import shutil
import subprocess
import argparse
from datetime import datetime

# -----------------------
# Utility functions
# -----------------------

def run_exiftool(folder):
    """Run exiftool to extract all tags as JSON."""
    res = subprocess.run(
        ["exiftool", "-j", "-charset", "utf8", folder],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    if res.returncode != 0:
        print("ExifTool error:", res.stderr.decode("utf-8"))
        return []
    try:
        return json.loads(res.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        print("Could not decode ExifTool JSON output.")
        return []

def clean_filename(name):
    """Remove problematic filesystem characters, normalize spacing."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def normalize_str(s):
    """Remove diacritics and normalize to UTF-8 ASCII for tagging."""
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )

def detect_intro_track(filename):
    """Detect if track is an intro (00 prefix or 'intro/uvod')."""
    base = os.path.basename(filename).lower()
    return base.startswith("00") or "uvod" in base or "intro" in base

def parse_folder_metadata(folder):
    """Guess author, year, and title from folder name."""
    folder_name = os.path.basename(folder)
    author, year, title = None, None, None

    parts = [p.strip() for p in folder_name.split(" - ")]
    if len(parts) == 3:
        author, year_part, title = parts
        year_match = re.search(r'\((\d{4})\)', year_part)
        if year_match:
            year = year_match.group(1)
    elif len(parts) == 2:
        author, title = parts

    return {
        "author": normalize_str(author) if author else None,
        "year": year,
        "title": normalize_str(title) if title else None
    }

def collect_existing_cover(folder):
    """Find any image files in folder to use as album art."""
    images = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                images.append(os.path.join(root, f))
    return images[0] if images else None

# -----------------------
# Interactive tagging
# -----------------------

def interactive_select(field, original, suggestion):
    """Ask user to accept/keep/change tag field."""
    print(f"\n{field}:")
    print(f"  Original:   {original}")
    print(f"  Suggestion: {suggestion}")
    print("Options: [a]ccept  [k]eep original  [m]anual from original  [s]tart from suggestion")
    choice = input("> ").strip().lower()
    if choice == "a":
        return suggestion
    elif choice == "k":
        return original
    elif choice == "m":
        return input(f"Enter new value (starting from original '{original}'): ")
    elif choice == "s":
        return input(f"Enter new value (starting from suggestion '{suggestion}'): ")
    else:
        print("Invalid input, keeping original.")
        return original

# -----------------------
# Main Processing
# -----------------------

def main(folder, rename_files=False, renumber_tracks=False, skip_intro=True):
    print(f"Scanning folder: {folder}")

    meta = run_exiftool(folder)
    if not meta:
        print("No metadata found.")
        return

    # Album-level detection
    folder_info = parse_folder_metadata(folder)
    cover_art = collect_existing_cover(folder)
    album_artist = folder_info.get("author")
    album_title = folder_info.get("title")
    pub_year = folder_info.get("year")

    # Confirm with user interactively
    album_artist = interactive_select("Album Artist", "", album_artist or "")
    album_title = interactive_select("Album (Title)", "", album_title or "")
    pub_year = interactive_select("Publication Year", "", pub_year or "")

    genre = interactive_select("Genre", "", "Audiokniha")

    # Collect files and optionally rename / renumber
    updated_tags = []
    track_num = 1
    for entry in meta:
        fname = entry.get("SourceFile", "")
        print(f"\n--- {os.path.basename(fname)} ---")
        # Suggest performer if found in comment
        performer = None
        comment = entry.get("Comment", "")
        if comment and re.search(r'(cte|čte|read by)', comment, re.IGNORECASE):
            performer = normalize_str(comment)

        performer = interactive_select("Performer", comment, performer or "")
        title = normalize_str(entry.get("Title", os.path.splitext(os.path.basename(fname))[0]))

        # Optional renumbering
        if renumber_tracks and not (skip_intro and detect_intro_track(fname)):
            track_tag = str(track_num).zfill(2)
            track_num += 1
        else:
            track_tag = entry.get("Track", "")

        updated_tags.append({
            "file": fname,
            "title": title,
            "track": track_tag,
            "album": album_title,
            "albumartist": album_artist,
            "artist": album_artist,
            "performer": performer,
            "genre": genre,
            "year": pub_year
        })

        # Optional rename
        if rename_files:
            newname = clean_filename(f"{track_tag} {title}.mp3")
            newpath = os.path.join(os.path.dirname(fname), newname)
            if fname != newpath:
                shutil.move(fname, newpath)
                print(f"Renamed: {fname} -> {newpath}")

    # Save JSON log for undo
    log_file = os.path.join(folder, "_tags_suggestions.json")
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(updated_tags, f, ensure_ascii=False, indent=2)
    print(f"\nSuggestions saved to {log_file}")

    # Also save CSV
    csv_file = os.path.join(folder, "_tags_report.csv")
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=updated_tags[0].keys())
        writer.writeheader()
        writer.writerows(updated_tags)
    print(f"CSV report saved to {csv_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze and retag audiobook folders.")
    parser.add_argument("folder", help="Folder path to analyze")
    parser.add_argument("--rename", action="store_true", help="Rename files from tags")
    parser.add_argument("--renumber", action="store_true", help="Renumber tracks")
    parser.add_argument("--no-skip-intro", action="store_true", help="Do not skip intro tracks (00) when renumbering")
    args = parser.parse_args()

    main(args.folder, rename_files=args.rename, renumber_tracks=args.renumber, skip_intro=not args.no_skip_intro)
