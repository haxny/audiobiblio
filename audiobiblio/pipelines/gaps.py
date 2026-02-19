"""
gaps — Gap analysis for catalog reconciliation.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from ..db.models import CatalogEntry, CatalogStatus, DownloadJob, JobStatus

log = structlog.get_logger()


def gap_report(session: Session, program_id: int) -> dict:
    """Generate gap report for a program.

    Returns:
    {
        total_catalog: int,
        matched_db: int,
        matched_file: int,
        downloaded: int,
        missing: int,
        watchable: int,  # missing but episode has WATCH job
        entries: [list of dicts sorted by episode_number]
    }
    """
    entries = (
        session.query(CatalogEntry)
        .filter(CatalogEntry.program_id == program_id)
        .order_by(CatalogEntry.episode_number.asc().nulls_last())
        .all()
    )

    total = len(entries)
    matched_db = sum(1 for e in entries if e.status == CatalogStatus.MATCHED_DB)
    matched_file = sum(1 for e in entries if e.status == CatalogStatus.MATCHED_FILE)
    downloaded = sum(1 for e in entries if e.status == CatalogStatus.DOWNLOADED)
    missing = sum(1 for e in entries if e.status == CatalogStatus.MISSING)

    # Count WATCH jobs for missing episodes
    missing_with_episode = [
        e for e in entries
        if e.status == CatalogStatus.MISSING and e.episode_id is not None
    ]
    watchable = 0
    if missing_with_episode:
        episode_ids = [e.episode_id for e in missing_with_episode]
        watch_count = (
            session.query(DownloadJob.episode_id)
            .filter(
                DownloadJob.episode_id.in_(episode_ids),
                DownloadJob.status == JobStatus.WATCH,
            )
            .distinct()
            .count()
        )
        watchable = watch_count

    entry_dicts = []
    for e in entries:
        entry_dicts.append({
            "id": e.id,
            "episode_number": e.episode_number,
            "title": e.title,
            "author": e.author,
            "year": e.year,
            "air_date": e.air_date.isoformat() if e.air_date else None,
            "source": e.source,
            "status": e.status,
            "local_file": e.local_file,
            "episode_id": e.episode_id,
        })

    return {
        "total_catalog": total,
        "matched_db": matched_db,
        "matched_file": matched_file,
        "downloaded": downloaded,
        "missing": missing,
        "watchable": watchable,
        "entries": entry_dicts,
    }
