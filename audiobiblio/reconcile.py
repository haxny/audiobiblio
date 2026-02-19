"""
reconcile — Scan local folders, match files to catalog entries and DB episodes.

Reuses:
- tags.reader.find_audio_files() / read_tags()
- dedupe._norm_title() / SequenceMatcher
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from .db.models import (
    Asset, AssetStatus, AssetType, CatalogEntry, CatalogStatus,
    Episode, Series, Work,
)
from .dedupe import _norm_title
from .tags.reader import find_audio_files, read_tags

log = structlog.get_logger()

# Regex to extract leading episode number from filename: "001 - Title.m4a"
_FILENAME_NUM_RE = re.compile(r"^(\d{1,4})\s*[-._)\]]\s*")
# Also try: "SFT_001_Title.m4a" or "SFT 001 Title.m4a"
_FILENAME_NUM_ALT_RE = re.compile(r"^\w+[_\s](\d{1,4})[_\s]")


def scan_folder(folder: str) -> list[dict]:
    """Scan folder for audio files and read their tags.

    Returns list of {path, tags, filename, episode_number, title_from_tags, title_from_filename}.
    """
    files = find_audio_files(folder)
    scanned = []
    for path in files:
        tags = read_tags(path)
        filename = os.path.splitext(os.path.basename(path))[0]

        # Extract episode number from tags
        tag_num = None
        for key in ("tracknumber", "track"):
            if key in tags:
                try:
                    # Handle "3/50" format
                    tag_num = int(str(tags[key]).split("/")[0])
                    break
                except (ValueError, IndexError):
                    pass

        # Extract episode number from filename
        file_num = None
        m = _FILENAME_NUM_RE.match(filename)
        if not m:
            m = _FILENAME_NUM_ALT_RE.match(filename)
        if m:
            file_num = int(m.group(1))

        episode_number = tag_num or file_num

        # Title from tags or filename
        title_from_tags = tags.get("title", "")
        title_from_filename = _FILENAME_NUM_RE.sub("", filename).strip()

        scanned.append({
            "path": path,
            "tags": tags,
            "filename": filename,
            "episode_number": episode_number,
            "title_from_tags": title_from_tags,
            "title_from_filename": title_from_filename,
        })

    log.info("folder_scanned", folder=folder, files=len(scanned))
    return scanned


def match_files_to_catalog(
    session: Session,
    program_id: int,
    scanned: list[dict],
) -> dict:
    """Match scanned files to CatalogEntry rows.

    Strategy:
    1. Track number match (episode_number == catalog episode_number)
    2. Title fuzzy match (_norm_title + SequenceMatcher > 0.85)
    3. Filename number extraction as fallback

    Returns {matched: [...], unmatched_files: [...], unmatched_catalog: [...]}.
    """
    catalog_entries = session.query(CatalogEntry).filter(
        CatalogEntry.program_id == program_id,
    ).all()

    # Build lookup by episode number
    by_number: dict[int, CatalogEntry] = {}
    for ce in catalog_entries:
        if ce.episode_number is not None:
            by_number[ce.episode_number] = ce

    # Build normalized title lookup
    title_entries: list[tuple[str, CatalogEntry]] = []
    for ce in catalog_entries:
        norm = _norm_title(ce.title)
        if norm:
            title_entries.append((norm, ce))

    matched = []
    unmatched_files = []
    matched_ids: set[int] = set()

    for item in scanned:
        entry = None
        match_method = None

        # Strategy 1: episode number match
        ep_num = item["episode_number"]
        if ep_num is not None and ep_num in by_number:
            entry = by_number[ep_num]
            match_method = "episode_number"

        # Strategy 2: fuzzy title match
        if not entry:
            best_ratio = 0.0
            best_entry = None
            # Try tag title first, then filename title
            for title_src in (item["title_from_tags"], item["title_from_filename"]):
                if not title_src:
                    continue
                norm_src = _norm_title(title_src)
                if not norm_src:
                    continue
                for norm_cat, ce in title_entries:
                    ratio = SequenceMatcher(None, norm_src, norm_cat).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_entry = ce
                if best_ratio > 0.85:
                    break
            if best_ratio > 0.85 and best_entry:
                entry = best_entry
                match_method = f"title_fuzzy({best_ratio:.2f})"

        if entry and entry.id not in matched_ids:
            entry.local_file = item["path"]
            if entry.status == CatalogStatus.MISSING:
                entry.status = CatalogStatus.MATCHED_FILE
            matched.append({
                "file": item["path"],
                "catalog_id": entry.id,
                "catalog_title": entry.title,
                "episode_number": entry.episode_number,
                "method": match_method,
            })
            matched_ids.add(entry.id)
        else:
            unmatched_files.append(item)

    # Unmatched catalog entries
    unmatched_catalog = [
        ce for ce in catalog_entries if ce.id not in matched_ids
    ]

    session.commit()
    log.info(
        "files_matched",
        program_id=program_id,
        matched=len(matched),
        unmatched_files=len(unmatched_files),
        unmatched_catalog=len(unmatched_catalog),
    )
    return {
        "matched": matched,
        "unmatched_files": unmatched_files,
        "unmatched_catalog": [
            {"id": ce.id, "episode_number": ce.episode_number, "title": ce.title}
            for ce in unmatched_catalog
        ],
    }


def match_catalog_to_episodes(session: Session, program_id: int) -> dict:
    """Match CatalogEntry rows to DB Episode rows.

    Uses episode_number, ext_id, and title fuzzy match.
    Returns {matched: int, unmatched: int}.
    """
    catalog_entries = session.query(CatalogEntry).filter(
        CatalogEntry.program_id == program_id,
        CatalogEntry.episode_id.is_(None),
    ).all()

    # Get all episodes for this program
    episodes = (
        session.query(Episode)
        .join(Work)
        .join(Series)
        .filter(Series.program_id == program_id)
        .all()
    )

    # Build lookups
    ep_by_number: dict[int, Episode] = {}
    ep_titles: list[tuple[str, Episode]] = []
    for ep in episodes:
        if ep.episode_number is not None:
            ep_by_number[ep.episode_number] = ep
        norm = _norm_title(ep.title)
        if norm:
            ep_titles.append((norm, ep))

    matched = 0
    for ce in catalog_entries:
        episode: Optional[Episode] = None

        # By episode number
        if ce.episode_number is not None and ce.episode_number in ep_by_number:
            episode = ep_by_number[ce.episode_number]

        # By fuzzy title
        if not episode:
            norm_cat = _norm_title(ce.title)
            if norm_cat:
                for norm_ep, ep in ep_titles:
                    if SequenceMatcher(None, norm_cat, norm_ep).ratio() > 0.85:
                        episode = ep
                        break

        if episode:
            ce.episode_id = episode.id
            if ce.status == CatalogStatus.MISSING:
                ce.status = CatalogStatus.MATCHED_DB
            matched += 1

    session.commit()
    log.info("catalog_episodes_matched", program_id=program_id, matched=matched)
    return {"matched": matched, "unmatched": len(catalog_entries) - matched}


def import_matched_files(session: Session, program_id: int) -> dict:
    """For catalog entries with matched files + matched episodes:
    create/update Asset records with file_path, set status to COMPLETE.
    Does NOT move files — just registers them.

    Returns {imported: int, skipped: int}.
    """
    entries = session.query(CatalogEntry).filter(
        CatalogEntry.program_id == program_id,
        CatalogEntry.local_file.isnot(None),
        CatalogEntry.episode_id.isnot(None),
    ).all()

    imported = 0
    skipped = 0

    for ce in entries:
        # Check if asset already exists
        existing = session.query(Asset).filter(
            Asset.episode_id == ce.episode_id,
            Asset.type == AssetType.AUDIO,
        ).first()

        if existing:
            if existing.status == AssetStatus.COMPLETE and existing.file_path:
                skipped += 1
                continue
            existing.file_path = ce.local_file
            existing.status = AssetStatus.COMPLETE
        else:
            asset = Asset(
                episode_id=ce.episode_id,
                type=AssetType.AUDIO,
                status=AssetStatus.COMPLETE,
                file_path=ce.local_file,
            )
            session.add(asset)

        ce.status = CatalogStatus.DOWNLOADED
        imported += 1

    session.commit()
    log.info("files_imported", program_id=program_id, imported=imported, skipped=skipped)
    return {"imported": imported, "skipped": skipped}
