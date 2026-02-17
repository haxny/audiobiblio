"""
postprocess — Tag, move to library, write ABS metadata after download.
"""
from __future__ import annotations
import shutil
from pathlib import Path
import structlog
from mutagen.mp4 import MP4, MP4Tags

from ..db.models import Episode, Work, Asset, AssetType, AssetStatus
from ..db.session import get_session
from .library import build_paths_for_episode
from .exporters import export_abs_metadata

log = structlog.get_logger()

AUDIO_EXTS = {".m4a", ".m4b", ".mp3", ".opus", ".ogg", ".aac", ".flac"}


def tag_audio(path: Path, ep: Episode, work: Work):
    """Write metadata tags to an audio file using mutagen."""
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".m4b", ".mp4"):
        audio = MP4(str(path))
        if audio.tags is None:
            audio.tags = MP4Tags()
        audio.tags['\xa9nam'] = [ep.title or '']
        audio.tags['\xa9ART'] = [work.author or '']
        audio.tags['\xa9alb'] = [work.title or '']
        if ep.episode_number is not None:
            audio.tags['trkn'] = [(ep.episode_number, 0)]
        audio.save()
        log.info("tagged", file=str(path))
    else:
        log.warning("tag_unsupported_format", suffix=suffix, file=str(path))


def move_to_library(src: Path, ep: Episode, work: Work, info: dict | None = None) -> Path:
    """Move audio file to its library path. Returns the new path."""
    paths = build_paths_for_episode(ep, work, info)
    dest_dir: Path = paths["base_dir"]
    stem: str = paths["stem"]
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / f"{stem}{src.suffix}"
    if dest.exists() and dest != src:
        log.warning("overwriting", dest=str(dest))
    shutil.move(str(src), str(dest))
    log.info("moved_to_library", src=str(src), dest=str(dest))
    return dest


def postprocess_episode(session, episode_id: int, audio_path: str | Path) -> Path | None:
    """
    Full post-download pipeline for one episode:
    1. Tag with mutagen
    2. Move to library path
    3. Write ABS metadata.json
    4. Update Asset in DB
    """
    s = session
    ep = s.get(Episode, episode_id)
    if not ep:
        log.error("episode_not_found", id=episode_id)
        return None

    work = s.get(Work, ep.work_id)
    if not work:
        log.error("work_not_found", id=ep.work_id)
        return None

    src = Path(audio_path)
    if not src.exists():
        log.error("audio_not_found", path=str(src))
        return None

    # 1. Tag
    tag_audio(src, ep, work)

    # 2. Move to library
    dest = move_to_library(src, ep, work)

    # 3. ABS metadata
    try:
        export_abs_metadata(s, work.id, str(dest.parent))
    except Exception as e:
        log.warning("abs_metadata_failed", error=str(e))

    # 4. Update Asset in DB
    asset = s.query(Asset).filter_by(
        episode_id=episode_id, type=AssetType.AUDIO
    ).first()
    if asset:
        asset.status = AssetStatus.COMPLETE
        asset.file_path = str(dest.resolve())
        asset.size_bytes = dest.stat().st_size
    s.commit()

    log.info("postprocess_done", episode=episode_id, dest=str(dest))
    return dest
