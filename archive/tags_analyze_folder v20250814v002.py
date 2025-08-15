# metadata.py
from .utils import (
    sanitize_filename, clean_tag_text, strip_diacritics, safe_int, safe_year,
    join_nonempty, extract_station_code
)

def build_album(series, book_number, book_title):
    nn = f"{safe_int(book_number):02d}" if book_number else "00"
    return f"{clean_tag_text(series)} - {nn} {clean_tag_text(book_title)}"

def build_version_parentheses(reader=None, publisher=None, extra=None, ascii_for_filename=True):
    bits = []
    if reader:
        bits.append(f"cte {reader}")
    if publisher:
        bits.append(publisher)
    if extra:
        bits.append(extra)
    if not bits:
        return "", ""
    version_text = ", ".join(bits)
    # For filename: ascii; for tags: keep accents.
    version_paren_file = f" ({sanitize_filename(version_text, ascii_for_filename)})"
    version_paren_tag = f" ({clean_tag_text(version_text)})"
    return version_paren_file, version_paren_tag

def build_track_code(book_number, track_number):
    """
    DTT when series (book_number present). Otherwise 2-digit track.
    Examples: book 3, track 1 -> '301'; no series, track 7 -> '07'
    """
    disc = safe_int(book_number, 0)
    track = safe_int(track_number, 0)
    if disc > 0:
        return f"{disc}{track:02d}"
    return f"{track:02d}"

def build_episode_filename(
    author, pub_year, series, book_number, book_title,
    reader=None, track_number=1, episode_title=None, part_number=None, ext=".mp3",
):
    nn = f"{safe_int(book_number):02d}" if book_number else "00"
    # Left side (ascii-only for stable cross-platform filenames)
    left = (
        f"{sanitize_filename(author)} - "
        f"({safe_year(pub_year)}) "
        f"{sanitize_filename(series)} {nn} - {sanitize_filename(book_title)}"
    )
    version_file, _ = build_version_parentheses(reader=reader, ascii_for_filename=True)
    code = build_track_code(book_number, track_number)
    tail = f" - {code}"
    title_part = ""
    if episode_title:
        title_part += f" {sanitize_filename(episode_title)}"
        if part_number is not None:
            title_part += f" {safe_int(part_number):02d}"
    # Ensure extension starts with a dot
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{left}{version_file}{tail}{title_part}{ext}"

def build_id3_tags(meta):
    """
    Returns a dict of tag fields (mutagen-friendly keys).
    - artist: Writer; Translator
    - albumartist: Writer
    - album: Series - NN BookTitle
    - title: Episode/Chapter title when available (fallback: 'Series NN - BookTitle')
    - TDRC/year: publication year
    - TRCK/tracknumber: nn
    - TPOS/discnumber: book_number
    - comment: version info (reader, publisher, etc.)
    - TXXX:Narrator: reader (if supported by your tagger)
    """
    writer = meta.get("writer") or meta.get("author") or meta.get("albumartist") or ""
    translator = meta.get("translator") or ""
    reader = meta.get("reader") or meta.get("narrator") or ""
    publisher = meta.get("publisher") or meta.get("label") or ""
    extra_version = meta.get("version_note") or ""

    series = meta.get("series") or meta.get("album") or ""
    book_title = meta.get("book_title") or meta.get("title") or ""
    book_number = meta.get("book_number") or meta.get("discnumber") or 0
    track_number = meta.get("track_number") or meta.get("track") or 1

    pub_year = (
        meta.get("publication_year")
        or meta.get("book_year")
        or safe_year(meta.get("original_date") or meta.get("date"))
    )

    album = build_album(series, book_number, book_title)
    _, version_paren_tag = build_version_parentheses(reader=reader, publisher=publisher, extra=extra_version, ascii_for_filename=False)

    # Title preference: explicit episode/chapter title, else "Series NN - BookTitle"
    nn = f"{safe_int(book_number):02d}" if book_number else "00"
    title = meta.get("episode_title") or meta.get("chapter_title")
    if title:
        title = clean_tag_text(title)
    else:
        title = clean_tag_text(f"{series} {nn} - {book_title}")

    # Artist & Album Artist
    artist = join_nonempty([clean_tag_text(writer), clean_tag_text(translator)], sep="; ")
    albumartist = clean_tag_text(writer)

    tags = {
        "title": title,
        "album": clean_tag_text(album),
        "artist": artist,
        "albumartist": albumartist,
        "date": safe_year(pub_year),
        "originaldate": safe_year(pub_year),
        "tracknumber": safe_int(track_number),
        "discnumber": safe_int(book_number),
        "comment": version_paren_tag[2:-1] if version_paren_tag else "",  # strip surrounding parens in comment
    }

    # Optional extras for richer tools (ID3 TXXX frames when supported)
    if reader:
        tags["narrator"] = clean_tag_text(reader)       # map to TXXX:Narrator in your tagger
    if publisher:
        tags["publisher"] = clean_tag_text(publisher)

    return tags

def enrich_metadata(meta: dict):
    """
    Main entry: returns original meta plus:
      - album (Series - NN BookTitle)
      - episode_filename (Author - (YEAR) Series NN - BookTitle (cte Reader) - DTT [Title [PP]].ext)
      - id3 (dict of tags; artist includes translator after writer)
      - station_code
    """
    author = meta.get("writer") or meta.get("author") or meta.get("albumartist") or "Unknown Author"
    pub_year = meta.get("publication_year") or meta.get("book_year") or meta.get("year") or "0000"
    series = meta.get("series") or meta.get("album_series") or meta.get("series_name") or meta.get("album") or "UnknownSeries"
    book_title = meta.get("book_title") or meta.get("album_book") or meta.get("album") or "UnknownBook"
    book_number = meta.get("book_number") or meta.get("discnumber") or 0
    reader = meta.get("reader") or meta.get("narrator")
    track_number = meta.get("track_number") or meta.get("track") or 1
    episode_title = meta.get("episode_title") or meta.get("chapter_title")
    part_number = meta.get("part_number")
    ext = f".{meta.get('ext', 'mp3')}" if not str(meta.get("ext", "")).startswith(".") else meta.get("ext")

    station_code = extract_station_code(meta.get("webpage_url", ""))

    album = build_album(series, book_number, book_title)
    filename = build_episode_filename(
        author=author, pub_year=pub_year, series=series, book_number=book_number,
        book_title=book_title, reader=reader, track_number=track_number,
        episode_title=episode_title, part_number=part_number, ext=ext,
    )
    id3 = build_id3_tags({
        **meta,
        "writer": author,
        "publication_year": pub_year,
        "series": series,
        "book_title": book_title,
        "book_number": book_number,
        "reader": reader,
        "track_number": track_number,
    })

    out = meta.copy()
    out.update({
        "station_code": station_code,
        "album": album,
        "episode_filename": filename,
        "id3": id3,
    })
    return out
