"""cover — embedded cover art for a work's audio files.

The files are the source of truth for artwork (ABS reads embedded art);
`cover_url` provenance rows keep the candidate gallery (one row per
source: databazeknih, radioteka, rozhlas, user upload/link).
"""
from __future__ import annotations

from pathlib import Path

import structlog

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode

log = structlog.get_logger()

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG"


def sniff_mime(data: bytes) -> str:
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    return "image/jpeg"


def extract_embedded_cover(path: str) -> tuple[bytes, str] | None:
    """Return (data, mime) of the embedded cover, or None."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix in (".m4a", ".m4b", ".mp4"):
            from mutagen.mp4 import MP4
            covr = (MP4(path).tags or {}).get("covr")
            if covr:
                data = bytes(covr[0])
                return data, sniff_mime(data)
        elif suffix == ".mp3":
            from mutagen.id3 import ID3
            for frame in ID3(path).getall("APIC"):
                return frame.data, frame.mime or sniff_mime(frame.data)
    except Exception:
        log.debug("cover_extract_failed", path=path, exc_info=True)
    return None


def _embed_one(path: str, data: bytes, mime: str) -> bool:
    suffix = Path(path).suffix.lower()
    try:
        if suffix in (".m4a", ".m4b", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            m = MP4(path)
            m["covr"] = [MP4Cover(data, imageformat=fmt)]
            m.save()
            return True
        if suffix == ".mp3":
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.delall("APIC")
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="cover", data=data))
            id3.save(path)
            return True
    except Exception:
        log.warning("cover_embed_failed", path=path, exc_info=True)
    return False


def work_audio_paths(session, work_id: int) -> list[str]:
    rows = (
        session.query(Asset.file_path)
        .join(Episode, Asset.episode_id == Episode.id)
        .filter(Episode.work_id == work_id, Asset.type == AssetType.AUDIO,
                Asset.status == AssetStatus.COMPLETE, Asset.file_path.isnot(None))
        .all()
    )
    return [p for (p,) in rows if p and Path(p).exists()]


def embed_cover_for_work(session, work_id: int, data: bytes) -> int:
    """Embed *data* as cover into every COMPLETE audio file of the work.
    Returns the number of files updated."""
    mime = sniff_mime(data)
    return sum(_embed_one(p, data, mime) for p in work_audio_paths(session, work_id))


def get_work_cover(session, work_id: int) -> tuple[bytes, str] | None:
    """Embedded cover of the first (lowest-numbered) audio file."""
    for p in sorted(work_audio_paths(session, work_id)):
        found = extract_embedded_cover(p)
        if found:
            return found
    return None
