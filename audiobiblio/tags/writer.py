"""
writer — Tag writing for all audio formats (MP3, M4A/M4B, FLAC, Ogg).

Single write_tags() entry point used by tag_fixer CLI, audioloader, and postprocess pipeline.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
import structlog

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX, APIC, TPUB, TPE2, COMM
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

log = structlog.get_logger()

_COVER_FILENAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png")


def find_cover_image(folder: str | Path) -> Optional[Path]:
    """Find a cover image in the given folder."""
    folder = Path(folder)
    for name in _COVER_FILENAMES:
        p = folder / name
        if p.exists():
            return p
    return None


def _set_txxx(id3: ID3, desc: str, value: str | None):
    """Set a TXXX frame (skip if empty/n/a)."""
    if not value or value == "n/a" or str(value).strip() == "":
        return
    keep = [f for f in id3.getall("TXXX") if getattr(f, "desc", "") != desc]
    id3.delall("TXXX")
    for f in keep:
        id3.add(f)
    id3.add(TXXX(encoding=1, desc=desc, text=str(value)))


def _write_mp3(
    path: str,
    album_tags: Dict[str, Any],
    track_tags: Dict[str, Any],
    cover_path: Optional[Path],
) -> None:
    """Write tags to MP3 (EasyID3 + raw ID3 for custom frames and cover)."""
    try:
        easy = EasyID3(path)
    except ID3NoHeaderError:
        easy = EasyID3()

    def _set(key: str, value: Any):
        if value not in (None, "", "n/a"):
            easy[key] = [str(value)]

    _set("album", album_tags.get("album"))
    _set("albumartist", album_tags.get("albumartist"))
    _set("artist", album_tags.get("artist"))
    _set("title", track_tags.get("title"))
    _set("genre", album_tags.get("genre") or "audiokniha")
    _set("date", album_tags.get("date"))
    _set("tracknumber", track_tags.get("tracknumber"))
    easy.save(v2_version=3, v1=0)

    # Custom frames
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()

    id3.delall("TPUB")
    if album_tags.get("publisher") not in (None, "", "n/a"):
        id3.add(TPUB(encoding=3, text=str(album_tags["publisher"])))

    id3.delall("TCOM")  # audiobooks don't have composers
    id3.delall("TPE3")  # wrong field for narrator

    id3.delall("TPE2")
    if album_tags.get("albumartist") not in (None, "", "n/a"):
        id3.add(TPE2(encoding=3, text=str(album_tags["albumartist"])))

    _set_txxx(id3, "Performer", album_tags.get("performer"))
    _set_txxx(id3, "Translator", album_tags.get("translator"))
    _set_txxx(id3, "DiscNumber", album_tags.get("discnumber"))
    _set_txxx(id3, "Description", album_tags.get("description"))
    _set_txxx(id3, "Comment", album_tags.get("comment"))
    _set_txxx(id3, "www", album_tags.get("www"))

    if cover_path and cover_path.exists():
        mime = "image/jpeg" if cover_path.suffix.lower() == ".jpg" else "image/png"
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover_path.read_bytes()))

    id3.save(v2_version=3, v1=0)


def _write_mp4(
    path: str,
    album_tags: Dict[str, Any],
    track_tags: Dict[str, Any],
    cover_path: Optional[Path],
) -> None:
    """Write tags to MP4/M4A/M4B."""
    mp4 = MP4(path)

    def _set(key: str, value: Any):
        if value not in (None, "", "n/a"):
            mp4[key] = [str(value)]

    _set("\xa9alb", album_tags.get("album"))
    _set("\xa9ART", album_tags.get("artist"))
    _set("aART", album_tags.get("albumartist"))
    _set("\xa9nam", track_tags.get("title"))
    _set("\xa9day", album_tags.get("date"))
    _set("\xa9gen", album_tags.get("genre") or "audiokniha")
    _set("\xa9cmt", album_tags.get("comment"))  # Standard iTunes comment atom

    tn = track_tags.get("tracknumber")
    if tn not in (None, "", "n/a"):
        try:
            mp4["trkn"] = [(int(str(tn).split("/")[0]), 0)]
        except (ValueError, TypeError):
            pass

    # Custom freeform atoms
    for k in ("translator", "publisher", "performer", "discnumber", "description", "comment", "www"):
        v = album_tags.get(k)
        if v not in (None, "", "n/a"):
            mp4[f"----:com.audiobiblio:{k.capitalize()}"] = [str(v).encode("utf-8")]

    if cover_path and cover_path.exists():
        data = cover_path.read_bytes()
        fmt = MP4Cover.FORMAT_PNG if cover_path.suffix.lower() == ".png" else MP4Cover.FORMAT_JPEG
        mp4["covr"] = [MP4Cover(data, imageformat=fmt)]

    mp4.save()


def _write_vorbis(audio, album_tags: Dict[str, Any], track_tags: Dict[str, Any]) -> None:
    """Write Vorbis-style tags (FLAC/Ogg Vorbis/Opus)."""
    def _set(key: str, value: Any):
        if value not in (None, "", "n/a"):
            audio[key] = [str(value)]
        elif key in audio:
            del audio[key]

    _set("album", album_tags.get("album"))
    _set("albumartist", album_tags.get("albumartist"))
    _set("artist", album_tags.get("artist"))
    _set("title", track_tags.get("title"))
    _set("tracknumber", track_tags.get("tracknumber"))
    _set("date", album_tags.get("date"))
    _set("genre", album_tags.get("genre") or "audiokniha")
    _set("publisher", album_tags.get("publisher"))
    _set("performer", album_tags.get("performer"))
    _set("translator", album_tags.get("translator"))
    _set("discnumber", album_tags.get("discnumber"))
    _set("comment", album_tags.get("comment"))
    _set("description", album_tags.get("description"))
    _set("www", album_tags.get("www"))
    audio.save()


def write_tags(
    path: str | Path,
    album_tags: Dict[str, Any],
    track_tags: Dict[str, Any],
    cover_path: str | Path | None = None,
) -> None:
    """
    Write tags to any supported audio file.

    This is the single entry point for all tag writing in the project.
    Dispatches to format-specific writers based on file extension.
    """
    path = str(path)
    ext = Path(path).suffix.lower()
    cp = Path(cover_path) if cover_path else None

    if ext == ".mp3":
        _write_mp3(path, album_tags, track_tags, cp)
    elif ext in (".m4a", ".m4b", ".mp4", ".aac"):
        _write_mp4(path, album_tags, track_tags, cp)
    elif ext == ".flac":
        audio = FLAC(path)
        _write_vorbis(audio, album_tags, track_tags)
    elif ext in (".ogg", ".opus"):
        try:
            audio = OggVorbis(path)
        except Exception:
            audio = OggOpus(path)
        _write_vorbis(audio, album_tags, track_tags)
    else:
        log.warning("write_tags_unsupported", ext=ext, path=path)


def write_comment_mp3(path: str, text: str, lang: str = "eng", desc: str = "") -> None:
    """Write ID3v2.3 Comment frame (UTF-16 for mp3tag/foobar2000 compatibility)."""
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()

    for frame in list(id3.getall("COMM")):
        if getattr(frame, "lang", None) == lang and getattr(frame, "desc", None) == desc:
            id3.delall("COMM")
            break

    id3.add(COMM(encoding=1, lang=lang, desc=desc, text=text))
    id3.save(v2_version=3)
