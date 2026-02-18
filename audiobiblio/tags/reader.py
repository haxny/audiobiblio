"""
reader — Tag reading from audio files (mutagen + exiftool fallback for M4A).
"""
from __future__ import annotations
import json
import os
import subprocess
from typing import Any, Dict, List
import structlog
from mutagen import File as MutagenFile
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, ID3NoHeaderError

from .diacritics import fix_windows1250

log = structlog.get_logger()

# Album-level tag names we track
ALBUM_TAG_NAMES = (
    "album", "albumartist", "artist", "performer", "translator",
    "publisher", "genre", "date", "discnumber", "comment", "description", "www",
)

# ID3 frame → common name mapping (for reference)
TAG_MAP_ALBUM = {
    "album": "TALB",
    "albumartist": "TPE2",
    "artist": "TPE1",
    "performer": "TPE3",
    "translator": "TXXX:Translator",
    "publisher": "TPUB",
    "genre": "TCON",
    "date": "TDRC",
    "discnumber": "TXXX:DiscNumber",
    "comment": "TXXX:Comment",
    "description": "TXXX:Description",
}

TAG_MAP_TRACK = {
    "title": "TIT2",
    "tracknumber": "TRCK",
}

# Exiftool → common name mapping
_EXIFTOOL_MAP = {
    "Album": "album",
    "AlbumArtist": "albumartist",
    "Artist": "artist",
    "Performer": "performer",
    "Translator": "translator",
    "Publisher": "publisher",
    "Title": "title",
    "Genre": "genre",
    "Date": "date",
    "ContentCreateDate": "date",
    "TrackNumber": "tracknumber",
    "DiscNumber": "discnumber",
    "Comment": "comment",
    "Description": "description",
    "Www": "www",
}

SUPPORTED_AUDIO_EXTS = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac")


def _read_exiftool_tags(filename: str) -> Dict[str, str]:
    """Read tags from M4A/MP4 using exiftool (more reliable than mutagen for these)."""
    tags: Dict[str, str] = {}
    try:
        subprocess.run(['exiftool', '-ver'], check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return tags

    try:
        result = subprocess.run(
            ['exiftool', '-json', '-A', filename],
            check=True, capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        if data and isinstance(data, list) and data:
            exif = data[0]
            for exif_key, common_key in _EXIFTOOL_MAP.items():
                if exif_key in exif:
                    tags[common_key] = str(exif[exif_key])
    except Exception as e:
        log.error("exiftool_read_failed", file=filename, error=str(e))
    return tags


def read_tags(filename: str) -> Dict[str, Any]:
    """Read tags from any supported audio file. Returns dict of common tag names → values."""
    tags: Dict[str, Any] = {}
    try:
        audio = MutagenFile(filename)
        if not audio:
            return tags

        if isinstance(audio, MP4):
            return _read_exiftool_tags(filename)

        # MP3/FLAC/Ogg: try easy mode first
        try:
            easy = MutagenFile(filename, easy=True)
            for k, v in easy.items():
                if v:
                    value = fix_windows1250(str(v[0]))
                    tags[k] = value
        except Exception:
            if hasattr(audio, 'tags') and audio.tags:
                for k, v in audio.tags.items():
                    if isinstance(v, list) and v:
                        tags[k.lower()] = fix_windows1250(str(v[0]))
                    else:
                        tags[k.lower()] = fix_windows1250(str(v))

        # Also read TXXX frames from MP3
        try:
            id3 = ID3(filename)
            for frame in id3.getall("TXXX"):
                desc = getattr(frame, "desc", "")
                text = getattr(frame, "text", [])
                if desc and text:
                    tags[desc.lower()] = str(text[0]) if isinstance(text, list) else str(text)
        except (ID3NoHeaderError, Exception):
            pass

    except Exception as e:
        log.error("read_tags_failed", file=filename, error=str(e))
    return tags


def aggregate_album_tags(files: List[str]) -> Dict[str, str]:
    """Read tags from files to find the most complete set of album-level tags."""
    album_tags: Dict[str, str] = {}
    for f in files:
        tags = read_tags(f)
        for tag in ALBUM_TAG_NAMES:
            if tag in tags and tag not in album_tags:
                album_tags[tag] = tags[tag]
        if all(t in album_tags for t in ("album", "albumartist", "genre")):
            break
    return album_tags


def find_audio_files(folder: str) -> List[str]:
    """Find all supported audio files in folder (recursive, sorted)."""
    files = []
    for root, _, filenames in os.walk(folder):
        for fn in filenames:
            if os.path.splitext(fn.lower())[1] in SUPPORTED_AUDIO_EXTS:
                files.append(os.path.join(root, fn))
    return sorted(files)
