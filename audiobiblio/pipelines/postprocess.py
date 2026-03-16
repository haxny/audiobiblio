"""
postprocess — Tag, move to library, write ABS metadata after download.

Uses the shared audiobiblio.tags package for all tag operations.
"""
from __future__ import annotations
import shutil
from pathlib import Path
import structlog

from sqlalchemy import select, func
from unidecode import unidecode

from ..db.models import Episode, Work, Asset, AssetType, AssetStatus, Series, Program
from ..db.session import get_session
from ..tags.writer import write_tags
from ..tags.genre import process_genre
from ..tags.nfo import write_nfo
from .library import build_paths_for_episode
from .exporters import export_abs_metadata
from .html_scraper import scrape_episode_html, build_comment
from ..tags.reader import read_tags

log = structlog.get_logger()

AUDIO_EXTS = {".m4a", ".m4b", ".mp3", ".opus", ".ogg", ".aac", ".flac"}


def _u(s: str | None) -> str:
    """Unidecode a string (strip diacritics). Returns empty string for None."""
    if not s:
        return ""
    return unidecode(s)


def _lookup_program_genre(work: Work) -> str:
    """Look up Program.genre via Work -> Series -> Program chain."""
    try:
        series = work.series
        if series and series.program and series.program.genre:
            return series.program.genre
    except Exception:
        pass
    return ""


def _lookup_program(work: Work) -> Program | None:
    """Get Program via Work -> Series -> Program chain."""
    try:
        series = work.series
        if series and series.program:
            return series.program
    except Exception:
        pass
    return None


def _count_episodes_in_work(ep: Episode) -> int:
    """Count total episodes in the same work (for tracknumber 'N of Total')."""
    try:
        from ..db.session import get_session
        s = get_session()
        count = s.query(func.count(Episode.id)).filter(
            Episode.work_id == ep.work_id
        ).scalar()
        s.close()
        return count or 0
    except Exception:
        return 0


def _find_html_asset(ep: Episode) -> Path | None:
    """Find the saved HTML webpage asset for an episode."""
    try:
        s = get_session()
        asset = s.query(Asset).filter_by(
            episode_id=ep.id, type=AssetType.WEBPAGE, status=AssetStatus.COMPLETE
        ).first()
        if asset and asset.file_path:
            p = Path(asset.file_path)
            if p.exists():
                return p
        s.close()
    except Exception:
        pass
    return None


def _build_www(ep: Episode, program: Program | None) -> str:
    """Build semicolon-separated URL list for www tag."""
    urls = []
    if ep.url:
        urls.append(ep.url)
    if program and program.url and program.url not in urls:
        urls.append(program.url)
    return ";".join(urls)


def _build_date(ep: Episode, work: Work) -> str:
    """Build date tag — prefer work year, fall back to episode published_at year."""
    if work.year:
        return str(work.year)
    if ep.published_at:
        return str(ep.published_at.year)
    return ""


def _truncate_to_main_title(title: str) -> str:
    """Truncate a title to just the main part, removing subtitle after first period.

    'Inu, mládí je mládí. Románek o živelné lásce...' -> 'Inu, mládí je mládí'
    'Denní host, Nocturno. Dvě nestárnoucí...' -> 'Denní host, Nocturno'
    """
    if not title:
        return title
    # Split on period followed by space and uppercase (subtitle pattern)
    dot_idx = title.find(". ")
    if dot_idx > 3:
        return title[:dot_idx].strip()
    return title


def _extract_author_title(episode_title: str) -> tuple[str, str]:
    """Try to extract author and work title from episode titles like
    'Karel Hynek: Inu, mládí je mládí. Románek o živelné lásce...'

    Returns (author, remaining_title). If no colon separator found,
    returns ("", original_title).
    """
    if not episode_title:
        return "", ""
    # Look for "Author: Title" pattern (colon after what looks like a name)
    colon_idx = episode_title.find(":")
    if colon_idx > 0 and colon_idx < 60:
        author_part = episode_title[:colon_idx].strip()
        title_part = episode_title[colon_idx + 1:].strip()
        # Sanity: author should be 2-4 words (name)
        word_count = len(author_part.split())
        if 1 <= word_count <= 5 and title_part:
            return author_part, title_part
    return "", episode_title


def _build_publisher(program: Program | None, ep: Episode) -> str:
    """Build publisher tag: 'Station BroadcastYear'."""
    if not program:
        return ""
    station = getattr(program, "station", None)
    code = getattr(station, "code", "") if station else ""
    year = ""
    if ep.published_at:
        year = str(ep.published_at.year)
    if code and year:
        return f"{code} {year}"
    return code or ""


def _has_richer_tags(path: Path) -> bool:
    """Check if the file already has manually enriched tags that shouldn't be overwritten."""
    try:
        existing = read_tags(str(path))
    except Exception:
        return False
    # A substantial comment (>100 chars) indicates manual enrichment
    comment = existing.get("comment", "")
    if comment and len(comment) > 100:
        return True
    return False


def tag_audio(path: Path, ep: Episode, work: Work, force: bool = False):
    """Write metadata tags to an audio file using the shared tags package.

    All text values are unidecoded (diacritics stripped) for maximum
    compatibility across devices (Audiobookshelf, Plexamp, etc.).

    For anthology programs (where each episode is a standalone work),
    the author and album title are extracted from the episode title.

    If existing tags are richer than what automation would produce (e.g.
    manually enriched comment), skips tagging and logs a warning.
    Pass force=True to override this check.
    """
    if not force and _has_richer_tags(path):
        log.warning("skipped_richer_tags", file=str(path),
                     reason="existing tags appear manually enriched — pass force=True to overwrite")
        return
    raw_genre = _lookup_program_genre(work)
    program = _lookup_program(work)
    total = _count_episodes_in_work(ep)

    # Author: prefer work.author; if missing, try extracting from episode title
    author = work.author or ""
    album_title = work.title or ""
    ep_title = ep.title or ""

    is_anthology = False
    if not author and ep_title:
        extracted_author, extracted_title = _extract_author_title(ep_title)
        if extracted_author:
            author = extracted_author
            # For anthology programs, the extracted title IS the album
            # Truncate to main title (before first period that's followed by more text)
            album_title = _truncate_to_main_title(extracted_title)
            is_anthology = True

    # Track number: "N of Total" format
    if ep.episode_number is not None and total > 0:
        tracknumber = f"{ep.episode_number} of {total}"
    elif ep.episode_number is not None:
        tracknumber = str(ep.episode_number)
    else:
        tracknumber = ""

    # Title tag: only for multi-track works (chapters/episodes).
    # For anthology programs (each episode = standalone work) or single-track
    # works, leave title empty.
    track_title = ""
    if total > 1 and not is_anthology:
        track_title = _u(ep_title)

    # Scrape HTML asset for comment, performer, etc.
    html_path = _find_html_asset(ep)
    scraped = scrape_episode_html(html_path) if html_path else None

    # Build comment (diacritics preserved — this is the one field that keeps them)
    comment = ""
    if scraped:
        # Full title with subtitle (original diacritics)
        _, full_title_part = _extract_author_title(ep_title)
        main_title = _truncate_to_main_title(full_title_part)
        subtitle = full_title_part[len(main_title):].lstrip(". ") if len(full_title_part) > len(main_title) else ""

        www_urls = [u for u in _build_www(ep, program).split(";") if u]
        comment = build_comment(
            author=author,  # original diacritics
            full_title=main_title,
            subtitle=subtitle,
            scraped=scraped,
            extra_urls=www_urls,
        )

    # Performer from scraped HTML
    performer = ""
    if scraped and scraped.performer:
        performer = _u(scraped.performer)

    album_tags = {
        "album": _u(album_title),
        "artist": _u(author),
        "albumartist": _u(author),
        "genre": process_genre(raw_genre),
        "date": _build_date(ep, work),
        "publisher": _build_publisher(program, ep),
        "performer": performer,
        "comment": comment,
        "www": _build_www(ep, program),
    }
    track_tags = {
        "title": track_title,
        "tracknumber": tracknumber,
    }
    write_tags(path, album_tags, track_tags)
    log.info("tagged", file=str(path))


def build_canonical_filename(ep: Episode, work: Work) -> str:
    """Build the canonical filename stem from tags/DB data.

    Convention:
      Single-track:  Author - (year) Title
      Multi-track:   Author - (year) Title - 01
      No author:     (year) Title
      No year:       Author - Title
    """
    ep_title = ep.title or ""
    author = work.author or ""
    album_title = work.title or ""
    year = work.year

    # For anthology: extract author from episode title
    if not author and ep_title:
        extracted_author, extracted_title = _extract_author_title(ep_title)
        if extracted_author:
            author = extracted_author
            album_title = _truncate_to_main_title(extracted_title)

    if not year and ep.published_at:
        year = ep.published_at.year

    author_s = _u(author)
    album_s = _u(album_title)

    # Build stem
    if author_s and year:
        stem = f"{author_s} - ({year}) {album_s}"
    elif author_s:
        stem = f"{author_s} - {album_s}"
    elif year:
        stem = f"({year}) {album_s}"
    else:
        stem = album_s or "track"

    # Add track number for multi-track works
    total = _count_episodes_in_work(ep)
    if ep.episode_number is not None and total > 1:
        stem = f"{stem} - {ep.episode_number:02d}"

    # Truncate
    if len(stem) > 80:
        stem = stem[:80].rstrip(". ")

    return stem


def rename_audio(path: Path, ep: Episode, work: Work) -> Path:
    """Rename an audio file to the canonical naming convention. Returns new path."""
    canonical_stem = build_canonical_filename(ep, work)
    new_path = path.parent / f"{canonical_stem}{path.suffix}"

    if new_path == path:
        return path
    if new_path.exists():
        log.warning("rename_target_exists", src=str(path), dest=str(new_path))
        return path

    path.rename(new_path)
    log.info("renamed", src=path.name, dest=new_path.name)

    # Update asset record in DB
    try:
        s = get_session()
        asset = s.query(Asset).filter_by(
            episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE
        ).first()
        if asset:
            asset.file_path = str(new_path.resolve())
            s.commit()
        s.close()
    except Exception as e:
        log.warning("rename_db_update_failed", error=str(e))

    return new_path


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
