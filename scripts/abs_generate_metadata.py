#!/usr/bin/env python3
"""
Generate metadata.json files for Audiobookshelf book folders.

Data sources (in priority order):
1. Audio file tags (album, artist, performer/composer, year, genre, publisher)
2. TXT/NFO files in the book folder (narrator, description, series, etc.)
3. Folder name parsing (author, year, title)

Usage (run ON the NAS or via SSH):
    python3 scripts/abs_generate_metadata.py /volume3/eBOOKs/eBOOKs.fiction --dry-run
    python3 scripts/abs_generate_metadata.py /volume3/eBOOKs/eBOOKs.fiction
    python3 scripts/abs_generate_metadata.py /volume3/eBOOKs/eBOOKs.fiction --overwrite
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wma", ".wav"}
TEXT_EXTS = {".txt", ".nfo"}

# Patterns for structured lines in TXT/NFO files
# Czech and English variants
FIELD_PATTERNS: list[tuple[str, str]] = [
    # (metadata key, regex pattern for the line)
    ("narrators", r"(?:Čte|Cte|Účinkuje|Ucinkuje|Reads?|Narrator|Interpret|Vypráví|Vypravi):\s*(.+)"),
    ("series", r"(?:Série|Serie|Series):\s*(.+)"),
    ("genres", r"(?:Žánr|Zanr|Genre|Genres):\s*(.+)"),
    ("publishedYear", r"(?:Vydáno|Vydano|Published|Premiéra|Premiera):\s*(\d{4})"),
    ("publisher", r"(?:Vydáno|Vydano|Published):\s*\d{4},\s*(.+)"),
    ("translator", r"(?:Překlad|Preklad|Translation):\s*(.+)"),
    ("director", r"(?:Režie|Rezie|Director):\s*(.+)"),
    ("isbn", r"ISBN:\s*([\d\-Xx]+)"),
    ("language", r"(?:Jazyk vydání|Jazyk vydani|Language):\s*(.+)"),
    ("originalTitle", r"(?:Orig(?:\.|inální|inalni)?\s*název|Orig\.\s*name):\s*(.+?)(?:,\s*\d{4})?$"),
]


def extract_narrator_from_name(name: str) -> tuple[str, list[str]]:
    """
    Extract narrator(s) from parenthetical in folder/file name.

    Patterns:
        "Title (Narrator Name)2018(1h48m)" -> narrators=["Narrator Name"]
        "Title (cte Narrator Name)" -> narrators=["Narrator Name"]
        "Title (Narrator1 a Narrator2)2020(1h)" -> narrators=["Narrator1", "Narrator2"]
        "Title (Narrator1, Narrator2 &)2020" -> narrators=["Narrator1", "Narrator2"]

    Returns (cleaned_name, narrators_list).
    """
    narrators: list[str] = []

    # Match "(Name)year(duration)" or "(Name)(duration)" or "(Name)year"
    # The narrator parenthetical usually comes after the title and contains
    # a Czech name (capitalized, with possible diacritics)
    # Pattern: (one or more names possibly separated by "a", ",", "&")
    # followed by optional year and/or duration

    # First strip duration patterns like (1h48m), (53m), (2h4m), (28m37s)
    clean = re.sub(r"\([\d]+h[\d]*m?[\d]*s?\)", "", name)
    clean = re.sub(r"\([\d]+m[\d]*s?\)", "", clean)

    # Match "(cte/čte Name)" pattern
    m = re.search(r"\((?:cte|čte|[Čč]te)\s+(.+?)\)", clean)
    if m:
        narrator_str = m.group(1).strip()
        narrators = _split_narrators(narrator_str)
        # Remove the match from name
        clean = clean[:m.start()] + clean[m.end():]
        clean = re.sub(r"\s*\d{4}\s*$", "", clean).strip()
        return clean, narrators

    # Match "(Name Name)" pattern — more permissive for Czech diacritics
    # Look for last parenthetical that looks like a person name (2+ words, capitalized)
    # Pattern: (Word Word) possibly followed by year and/or duration
    narrator_pattern = (
        r"\("
        r"([A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+"  # First name (uppercase start)
        r"(?:\s+[A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+)*"  # Last name(s)
        r"(?:\s*[,&]\s*[A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+(?:\s+[A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+)*)*"  # More narrators
        r"(?:\s+a\s+[A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+(?:\s+[A-Z\u00C0-\u017E][a-z\u00E0-\u017E]+)*)*"  # "a" separator
        r"(?:\s*&\s*)?"  # Trailing &
        r")"
        r"\)"
        r"(?:\d{4})?"  # Optional year
        r"(?:\([^)]*\))?"  # Optional duration
    )
    m = re.search(narrator_pattern, clean)
    if m:
        narrator_str = m.group(1).strip().rstrip("&").strip()
        # Validate: at least one space (first+last name), not too long
        if " " in narrator_str and len(narrator_str) < 80:
            narrators = _split_narrators(narrator_str)
            # Remove entire match from name
            clean = clean[:m.start()] + clean[m.end():]
            # Remove trailing year
            clean = re.sub(r"\s*\d{4}\s*$", "", clean).strip()
            return clean, narrators

    # No narrator found
    return name, []


def _split_narrators(s: str) -> list[str]:
    """Split narrator string by 'a', ',' or '&'."""
    parts = re.split(r"\s+a\s+|,\s*|&\s*", s)
    return [p.strip() for p in parts if p.strip()]


def parse_folder_name(folder_name: str) -> dict:
    """
    Parse author, year, title, narrator from folder name.

    Supported patterns:
        "Author - (year) Title (Narrator)year(duration)"
        "Author - (year) Title"
        "Author - Title"
        "Title (year)"
        "Title"
    """
    result: dict = {}

    # Strip [audio] suffix
    name = re.sub(r"\s*\[audio\]\s*$", "", folder_name, flags=re.IGNORECASE).strip()

    # Extract narrator from parenthetical before other parsing
    name, narrators = extract_narrator_from_name(name)
    if narrators:
        result["narrators"] = narrators

    # Pattern: "Author - (year) Title"
    m = re.match(r"^(.+?)\s*-\s*\((\d{4})\)\s*(.+)$", name)
    if m:
        result["authors"] = [m.group(1).strip()]
        result["publishedYear"] = m.group(2)
        result["title"] = m.group(3).strip()
        return result

    # Pattern: "Author - Title"
    m = re.match(r"^(.+?)\s*-\s+(.+)$", name)
    if m:
        result["authors"] = [m.group(1).strip()]
        result["title"] = m.group(2).strip()
        return result

    # Pattern: "Title (year)"
    m = re.match(r"^(.+?)\s*\((\d{4})\)\s*$", name)
    if m:
        result["title"] = m.group(1).strip()
        result["publishedYear"] = m.group(2)
        return result

    # Fallback: entire name is the title
    result["title"] = name
    return result


def parse_parent_author(book_dir: Path) -> str | None:
    """Extract author from parent directory name (Author [audio] pattern)."""
    parent = book_dir.parent.name
    author = re.sub(r"\s*\[audio\]\s*$", "", parent, flags=re.IGNORECASE).strip()
    if author and author != parent:
        return author
    # Even without [audio], parent is likely the author in Author/Book structure
    return author if author else None


def read_audio_tags(audio_file: Path) -> dict:
    """Read tags from an audio file using mutagen."""
    try:
        import mutagen
    except ImportError:
        return _read_audio_tags_ffprobe(audio_file)

    try:
        audio = mutagen.File(str(audio_file))
        if audio is None or audio.tags is None:
            return {}

        raw_tags = audio.tags
        meta: dict = {}

        def _get_tag(keys: list[str]) -> str:
            """Get first matching tag value (single string)."""
            for key in keys:
                val = raw_tags.get(key)
                if val:
                    if isinstance(val, list):
                        return _decode_value(val[0])
                    return _decode_value(val)
            return ""

        def _decode_value(item: object) -> str:
            """Decode a single tag value (MP4FreeForm, bytes, or str)."""
            if isinstance(item, bytes):
                return item.decode('utf-8', errors='replace')
            try:
                # MP4FreeForm supports bytes() conversion
                return bytes(item).decode('utf-8', errors='replace')
            except (TypeError, UnicodeDecodeError):
                return str(item)

        def _get_tag_list(keys: list[str]) -> list[str]:
            """Get all values from matching tags as flat list."""
            result: list[str] = []
            for key in keys:
                val = raw_tags.get(key)
                if val:
                    if isinstance(val, list):
                        for item in val:
                            s = _decode_value(item)
                            # Split semicolons
                            result.extend(p.strip() for p in s.split(";") if p.strip())
                    else:
                        s = _decode_value(val)
                        result.extend(p.strip() for p in s.split(";") if p.strip())
            return result

        # M4A/MP4 tags use different keys than ID3
        # MP4: ©alb, ©ART, aART, ©day, ©gen, ©cmt, ©too
        # MP4 custom: ----:com.apple.iTunes:PERFORMER, etc.
        # ID3: TIT2, TALB, TPE1, TPE2, TDRC, TCON, COMM, TXXX:PERFORMER, etc.

        # Title from album
        album = _get_tag(["©alb", "TALB", "album"])
        if album:
            meta["title"] = album

        # Authors from album artist
        albumartist = _get_tag(["aART", "TPE2", "albumartist"])
        artist = _get_tag(["©ART", "TPE1", "artist"])
        if albumartist:
            meta["authors"] = [albumartist]
        elif artist:
            # Artist might have multiple values
            artists = _get_tag_list(["©ART", "TPE1", "artist"])
            meta["authors"] = [artists[0]] if artists else [artist]

        # Narrators from PERFORMER (custom iTunes atom or TXXX)
        performers = _get_tag_list([
            "----:com.apple.iTunes:PERFORMER",
            "TXXX:PERFORMER",
            "performer",
        ])
        if not performers:
            # Fallback: composer (ABS convention)
            performers = _get_tag_list([
                "©wrt", "TCOM", "composer",
            ])
        # Split narrators that are in old format: "Name1, Name2 a Name3"
        split_performers: list[str] = []
        for p in performers:
            # Split on " a " (Czech "and"), ", ", and ";"
            parts = re.split(r"\s+a\s+|,\s*|;\s*", p)
            split_performers.extend(part.strip() for part in parts if part.strip())
        if split_performers:
            meta["narrators"] = split_performers

        # Genre (custom iTunes atom or standard)
        genres = _get_tag_list([
            "----:com.apple.iTunes:GENRE",
            "©gen", "TCON", "genre",
        ])
        if genres:
            meta["genres"] = genres

        # Year
        date = _get_tag(["©day", "TDRC", "date"])
        if date:
            meta["publishedYear"] = str(date)[:4]

        # Publisher
        publisher = _get_tag([
            "----:com.apple.iTunes:PUBLISHER",
            "TPUB", "publisher",
        ])
        if publisher:
            meta["publisher"] = publisher

        # Description from comment
        comment = _get_tag(["©cmt", "comment"])
        # Also check COMM frames for ID3
        if not comment:
            for key in raw_tags:
                if key.startswith("COMM"):
                    comment = str(raw_tags[key])
                    break
        if comment and len(comment) > 100:
            meta["description"] = comment

        # URL
        www = _get_tag([
            "----:com.apple.iTunes:WWW",
            "WXXX:", "WOAR", "www",
        ])
        if www:
            meta["url"] = www

        # Translator
        translator = _get_tag([
            "----:com.apple.iTunes:TRANSLATOR",
            "TXXX:TRANSLATOR", "translator",
        ])
        if translator:
            meta["translator"] = translator

        return meta
    except Exception:
        return {}


def _read_audio_tags_ffprobe(audio_file: Path) -> dict:
    """Fallback: read tags using ffprobe if mutagen not available."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_file)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
        meta: dict = {}
        if tags.get("album"):
            meta["title"] = tags["album"]
        if tags.get("album_artist") or tags.get("albumartist"):
            meta["authors"] = [(tags.get("album_artist") or tags.get("albumartist", ""))]
        elif tags.get("artist"):
            meta["authors"] = [tags["artist"]]
        if tags.get("genre"):
            meta["genres"] = [g.strip() for g in tags["genre"].split(";")]
        if tags.get("date"):
            meta["publishedYear"] = tags["date"][:4]
        return meta
    except Exception:
        return {}


def parse_text_files(book_dir: Path) -> dict:
    """Parse TXT/NFO files for metadata."""
    meta: dict = {}
    description_parts: list[str] = []

    for f in sorted(book_dir.iterdir()):
        if f.suffix.lower() not in TEXT_EXTS:
            continue
        if f.name.startswith("."):
            continue

        try:
            # Try UTF-8 first, fallback to latin-1
            try:
                content = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = f.read_text(encoding="latin-1")
        except OSError:
            continue

        # Skip files that are just file listings or move notes
        if content.strip().startswith("moved to "):
            continue

        # Extract structured fields
        for key, pattern in FIELD_PATTERNS:
            if key in meta:
                continue  # Don't overwrite
            m = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if m:
                value = m.group(1).strip()
                if key == "narrators":
                    # Split multiple narrators: "Jan Novak a Eva Novakova" or "Jan Novak, Eva Novakova"
                    meta[key] = [n.strip() for n in re.split(r"\s+a\s+|,\s*", value) if n.strip()]
                elif key == "genres":
                    meta[key] = [g.strip() for g in value.split(",") if g.strip()]
                elif key == "publishedYear":
                    meta[key] = value
                else:
                    meta[key] = value

        # Use first substantial paragraph as description (if no audio tag description)
        if "description" not in meta:
            # Take text before the first structured field
            lines = content.split("\n")
            desc_lines: list[str] = []
            for line in lines:
                line_stripped = line.strip()
                # Stop at structured fields
                if any(re.match(p, line_stripped, re.IGNORECASE) for _, p in FIELD_PATTERNS):
                    break
                # Stop at file listings
                if line_stripped.endswith((".mp3", ".m4a", ".m4b", ".nfo", ".txt")):
                    break
                if line_stripped:
                    desc_lines.append(line_stripped)

            desc = "\n".join(desc_lines).strip()
            if len(desc) > 100:
                description_parts.append(desc)

    if description_parts and "description" not in meta:
        meta["description"] = description_parts[0]

    return meta


def build_metadata(book_dir: Path) -> dict:
    """Build complete metadata from all sources."""
    # 1. Parse folder name (lowest priority)
    folder_meta = parse_folder_name(book_dir.name)

    # Also try parent dir for author
    parent_author = parse_parent_author(book_dir)

    # 2. Parse text files
    text_meta = parse_text_files(book_dir)

    # 3. Read audio tags (highest priority)
    audio_meta: dict = {}
    audio_files = sorted(
        f for f in book_dir.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )
    if audio_files:
        audio_meta = read_audio_tags(audio_files[0])

    # Merge: audio > text > folder
    metadata: dict = {}

    # Title
    metadata["title"] = (
        audio_meta.get("title")
        or text_meta.get("title")
        or folder_meta.get("title")
        or book_dir.name
    )

    # Authors
    authors = (
        audio_meta.get("authors")
        or text_meta.get("authors")
        or folder_meta.get("authors")
    )
    if not authors and parent_author:
        authors = [parent_author]
    if authors:
        metadata["authors"] = authors

    # Narrators: audio tags > text files > folder name > description fallback
    narrators = audio_meta.get("narrators") or text_meta.get("narrators") or folder_meta.get("narrators")
    if not narrators:
        # Try extracting from description as last resort
        desc = audio_meta.get("description") or text_meta.get("description") or ""
        for _, pattern in FIELD_PATTERNS:
            if "Čte|Cte|Účinkuje" in pattern or "Reads" in pattern:
                m = re.search(pattern, desc, re.MULTILINE | re.IGNORECASE)
                if m:
                    val = m.group(1).strip()
                    narrators = [n.strip() for n in re.split(r"\s+a\s+|,\s*|;\s*", val) if n.strip()]
                    break
    if narrators:
        metadata["narrators"] = narrators

    # Year
    year = (
        audio_meta.get("publishedYear")
        or text_meta.get("publishedYear")
        or folder_meta.get("publishedYear")
    )
    if year:
        metadata["publishedYear"] = year

    # Other fields
    for key in ("publisher", "description", "genres", "isbn", "translator",
                "series", "language", "url", "originalTitle", "director"):
        val = audio_meta.get(key) or text_meta.get(key)
        if val:
            metadata[key] = val

    return metadata


def to_abs_metadata(meta: dict) -> dict:
    """Convert to ABS metadata.json format."""
    abs_meta: dict = {
        "title": meta.get("title", ""),
    }

    if meta.get("authors"):
        abs_meta["authors"] = meta["authors"]
    if meta.get("narrators"):
        abs_meta["narrators"] = meta["narrators"]
    if meta.get("publishedYear"):
        abs_meta["publishedYear"] = meta["publishedYear"]
    if meta.get("publisher"):
        abs_meta["publisher"] = meta["publisher"]
    if meta.get("description"):
        abs_meta["description"] = meta["description"]
    if meta.get("genres"):
        abs_meta["genres"] = meta["genres"]
    if meta.get("isbn"):
        abs_meta["isbn"] = meta["isbn"]
    if meta.get("series"):
        # ABS series format: plain strings like "Name #sequence"
        series_name = meta["series"]
        m = re.match(r"(.+?)\s+(\d+)\.?\s*$", series_name)
        if m:
            abs_meta["series"] = [f"{m.group(1).strip()} #{m.group(2)}"]
        else:
            abs_meta["series"] = [series_name]
    if meta.get("language"):
        abs_meta["language"] = meta["language"]

    return abs_meta


def find_book_dirs(library_root: Path) -> list[Path]:
    """Find all book directories (containing audio files)."""
    book_dirs: list[Path] = []

    for author_dir in sorted(library_root.iterdir()):
        if not author_dir.is_dir() or author_dir.name.startswith(("@", ".")):
            continue

        # Check if author_dir itself contains audio (flat structure)
        try:
            author_entries = list(author_dir.iterdir())
        except PermissionError:
            print(f"  SKIP (permission denied): {author_dir.name}", file=sys.stderr)
            continue

        has_audio = False
        for f in author_entries:
            try:
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                    has_audio = True
                    break
            except PermissionError:
                continue

        # Check subdirectories (Author/Book structure)
        has_subdirs = False
        for book_dir in sorted(author_entries):
            try:
                if not book_dir.is_dir() or book_dir.name.startswith(("@", ".")):
                    continue
            except PermissionError:
                continue
            has_subdirs = True
            try:
                sub_audio = any(
                    f.suffix.lower() in AUDIO_EXTS
                    for f in book_dir.iterdir()
                    if f.is_file()
                )
            except PermissionError:
                continue
            if sub_audio:
                book_dirs.append(book_dir)

        # If audio directly in author dir and no subdirs, treat author dir as book
        if has_audio and not has_subdirs:
            book_dirs.append(author_dir)

    return book_dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate metadata.json for ABS")
    parser.add_argument("library_root", help="Path to the library directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing metadata.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all metadata")
    args = parser.parse_args()

    library_root = Path(args.library_root)
    if not library_root.is_dir():
        print(f"ERROR: {library_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {library_root}...")
    book_dirs = find_book_dirs(library_root)
    print(f"Found {len(book_dirs)} book directories\n")

    created = 0
    skipped_exists = 0
    skipped_empty = 0
    errors = 0

    for book_dir in book_dirs:
        metadata_file = book_dir / "metadata.json"

        if metadata_file.exists() and not args.overwrite:
            skipped_exists += 1
            continue

        try:
            meta = build_metadata(book_dir)
        except PermissionError:
            print(f"  SKIP (permission): {book_dir.name}", file=sys.stderr)
            errors += 1
            continue

        if not meta.get("title"):
            skipped_empty += 1
            continue

        abs_meta = to_abs_metadata(meta)

        if args.dry_run:
            narr = abs_meta.get("narrators", [])
            author = abs_meta.get("authors", ["?"])
            print(f"  {author[0][:25]:25s} | {abs_meta['title'][:40]:40s} | narr: {narr}")
            if args.verbose:
                print(f"    {json.dumps(abs_meta, ensure_ascii=False)[:200]}")
            created += 1
            continue

        try:
            metadata_file.write_text(
                json.dumps(abs_meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            created += 1
            if created % 100 == 0:
                print(f"  ... {created} created")
        except OSError as e:
            print(f"  ERROR: {book_dir.name} — {e}", file=sys.stderr)
            errors += 1

    action = "Would create" if args.dry_run else "Created"
    print(f"\nDone: {action} {created} metadata.json files, "
          f"skipped {skipped_exists} existing, {skipped_empty} empty, {errors} errors.")


if __name__ == "__main__":
    main()
