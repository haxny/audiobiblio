"""
catalog — API endpoints for catalog reconciliation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...catalog import scrape_catalog, upsert_catalog
from ...pipelines.gaps import gap_report
from ...reconcile import (
    import_matched_files,
    match_catalog_to_episodes,
    match_files_to_catalog,
    scan_folder,
)
from ...db.models import CatalogEntry, CatalogStatus
from ..deps import get_db

import re
from datetime import datetime


def _parse_flexible_date(s: str) -> datetime | None:
    """Parse dates in various formats:
    YYYY-MM-DD, YYYYMMDD, DD.MM.YYYY, DD. MM. YYYY, D.M.YYYY
    """
    s = s.strip()
    if not s:
        return None
    # YYYY-MM-DD (ISO)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # YYYYMMDD
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # DD.MM.YYYY or DD. MM. YYYY or D.M.YYYY
    m = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None
from ..schemas import CatalogManualEntry, CatalogScanRequest, CatalogScrapeRequest

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get("/{program_id}")
def list_catalog(program_id: int, db: Session = Depends(get_db)):
    """List catalog entries + gap stats for a program."""
    return gap_report(db, program_id)


@router.post("/{program_id}/scrape")
def scrape(
    program_id: int,
    req: CatalogScrapeRequest,
    db: Session = Depends(get_db),
):
    """Trigger catalog scrape from a reference source."""
    try:
        entries = scrape_catalog(program_id, req.source, req.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = upsert_catalog(db, program_id, entries, req.source, req.url)
    # Auto-match to DB episodes after scrape
    ep_match = match_catalog_to_episodes(db, program_id)
    result["episode_matches"] = ep_match["matched"]
    return result


@router.post("/{program_id}/scan")
def scan(
    program_id: int,
    req: CatalogScanRequest,
    db: Session = Depends(get_db),
):
    """Scan a local folder and match files to catalog entries."""
    scanned = scan_folder(req.folder)
    if not scanned:
        return {"matched": [], "unmatched_files": [], "unmatched_catalog": []}
    result = match_files_to_catalog(db, program_id, scanned)
    return result


@router.post("/{program_id}/import")
def import_files(program_id: int, db: Session = Depends(get_db)):
    """Import matched files to DB as Assets."""
    return import_matched_files(db, program_id)


@router.post("/{program_id}/manual")
def manual_entry(
    program_id: int,
    entries: list[CatalogManualEntry],
    db: Session = Depends(get_db),
):
    """Create/update catalog entries manually from unmatched files."""
    from datetime import datetime

    created = 0
    updated = 0
    for e in entries:
        if not e.title.strip():
            continue

        existing = None
        if e.episode_number is not None:
            existing = db.query(CatalogEntry).filter(
                CatalogEntry.program_id == program_id,
                CatalogEntry.episode_number == e.episode_number,
            ).first()

        air_date = None
        if e.air_date:
            air_date = _parse_flexible_date(e.air_date)

        if existing:
            existing.title = e.title.strip()
            if e.file_path:
                existing.local_file = e.file_path
                if existing.status == CatalogStatus.MISSING:
                    existing.status = CatalogStatus.MATCHED_FILE
            if air_date:
                existing.air_date = air_date
                existing.year = air_date.year
            if e.source_url:
                existing.source_url = e.source_url
            if e.author:
                existing.author = e.author
            existing.updated_at = datetime.utcnow()
            updated += 1
        else:
            entry = CatalogEntry(
                program_id=program_id,
                episode_number=e.episode_number,
                title=e.title.strip(),
                author=e.author or None,
                air_date=air_date,
                year=air_date.year if air_date else None,
                source="manual",
                source_url=e.source_url or None,
                local_file=e.file_path or None,
                status=CatalogStatus.MATCHED_FILE if e.file_path else CatalogStatus.MISSING,
            )
            db.add(entry)
            created += 1

    db.commit()
    return {"created": created, "updated": updated}


@router.post("/{program_id}/from-db")
def catalog_from_db(program_id: int, db: Session = Depends(get_db)):
    """Auto-populate catalog entries from existing DB episodes for this program."""
    from ...db.models import Episode, Work, Series

    episodes = (
        db.query(Episode)
        .join(Work, Episode.work_id == Work.id)
        .join(Series, Work.series_id == Series.id)
        .filter(Series.program_id == program_id)
        .all()
    )

    created = 0
    skipped = 0
    for ep in episodes:
        # Check if already in catalog
        existing = db.query(CatalogEntry).filter(
            CatalogEntry.program_id == program_id,
            CatalogEntry.episode_id == ep.id,
        ).first()
        if existing:
            skipped += 1
            continue

        # Also check by episode number
        if ep.episode_number is not None:
            existing = db.query(CatalogEntry).filter(
                CatalogEntry.program_id == program_id,
                CatalogEntry.episode_number == ep.episode_number,
            ).first()
            if existing:
                existing.episode_id = ep.id
                if existing.status == CatalogStatus.MISSING:
                    existing.status = CatalogStatus.MATCHED_DB
                skipped += 1
                continue

        entry = CatalogEntry(
            program_id=program_id,
            episode_number=ep.episode_number,
            title=ep.title,
            air_date=ep.published_at,
            year=ep.published_at.year if ep.published_at else None,
            source="db",
            source_url=ep.url,
            episode_id=ep.id,
            status=CatalogStatus.MATCHED_DB,
        )
        db.add(entry)
        created += 1

    db.commit()
    return {"created": created, "skipped": skipped}


@router.get("/{program_id}/gaps")
def gaps(program_id: int, db: Session = Depends(get_db)):
    """Gap report for a program."""
    return gap_report(db, program_id)
