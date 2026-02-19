"""
postprocess — Tag, move to library, write ABS metadata after download.

Uses the shared audiobiblio.tags package for all tag operations.
"""
from __future__ import annotations
import shutil
from pathlib import Path
import structlog

from sqlalchemy import select

from ..db.models import Episode, Work, Asset, AssetType, AssetStatus, Series, Program
from ..db.session import get_session
from ..tags.writer import write_tags
from ..tags.genre import process_genre
from ..tags.nfo import write_nfo
from .library import build_paths_for_episode
from .exporters import export_abs_metadata

log = structlog.get_logger()

AUDIO_EXTS = {".m4a", ".m4b", ".mp3", ".opus", ".ogg", ".aac", ".flac"}


def _lookup_program_genre(work: Work) -> str:
    """Look up Program.genre via Work -> Series -> Program chain."""
    try:
        series = work.series
        if series and series.program and series.program.genre:
            return series.program.genre
    except Exception:
        pass
    return ""


def tag_audio(path: Path, ep: Episode, work: Work):
    """Write metadata tags to an audio file using the shared tags package."""
    raw_genre = _lookup_program_genre(work)
    album_tags = {
        "album": work.title or "",
        "artist": work.author or "",
        "albumartist": work.author or "",
        "genre": process_genre(raw_genre),
    }
    track_tags = {
        "title": ep.title or "",
        "tracknumber": str(ep.episode_number) if ep.episode_number is not None else "",
    }
    write_tags(path, album_tags, track_tags)
    log.info("tagged", file=str(path))


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
    1. Tag with shared tags package (all formats, genre taxonomy, role rules)
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

    # 5. NFO sidecar — generate if all episodes in the Work are downloaded
    _maybe_generate_nfo(s, work, dest.parent)

    log.info("postprocess_done", episode=episode_id, dest=str(dest))
    return dest


def _maybe_generate_nfo(session, work: Work, dest_dir: Path):
    """Generate .nfo sidecar if all episodes in the Work have completed audio assets."""
    episodes = session.scalars(
        select(Episode).where(Episode.work_id == work.id).order_by(Episode.episode_number)
    ).all()
    if not episodes:
        return

    # Check if all episodes have a COMPLETE audio asset
    all_complete = True
    for ep in episodes:
        audio = session.query(Asset).filter_by(
            episode_id=ep.id, type=AssetType.AUDIO
        ).first()
        if not audio or audio.status != AssetStatus.COMPLETE:
            all_complete = False
            break

    if not all_complete:
        return

    # Look up genre from Program
    genre = ""
    try:
        series = session.get(Series, work.series_id)
        if series:
            program = session.get(Program, series.program_id)
            if program and program.genre:
                genre = program.genre
    except Exception:
        pass

    album_tags = {
        "album": work.title or "",
        "artist": work.author or "",
        "genre": genre,
    }

    ep_dicts = []
    for ep in episodes:
        ep_dicts.append({
            "title": ep.title or "",
            "date": ep.published_at.strftime("%Y%m%d") if ep.published_at else "",
            "url": ep.url or "",
            "description": ep.summary or "",
            "duration": (ep.duration_ms / 1000) if ep.duration_ms else None,
        })

    try:
        nfo_path = write_nfo(dest_dir, album_tags, ep_dicts)
        log.info("nfo_written", path=str(nfo_path), episodes=len(episodes))
    except Exception as e:
        log.warning("nfo_write_failed", error=str(e))
