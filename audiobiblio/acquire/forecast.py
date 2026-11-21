"""Download volume and queue forecast, answered from the DB.

Real free disk space cannot be read inside the container (btrfs), so this only
covers what the DB knows: GB taken in per day, average file size, and the size
of the pending queue. The space watch compares it with the real free space.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Asset, AssetType, DownloadJob, JobStatus


def download_forecast(session: Session, now: datetime | None = None, days: int = 7) -> dict:
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    done = (session.query(func.count(DownloadJob.id), func.coalesce(func.sum(Asset.size_bytes), 0))
            .join(Asset, (Asset.episode_id == DownloadJob.episode_id) & (Asset.type == AssetType.AUDIO))
            .filter(DownloadJob.status == JobStatus.SUCCESS,
                    DownloadJob.finished_at >= since, DownloadJob.finished_at <= now,
                    Asset.size_bytes.isnot(None)).one())
    files, total = int(done[0]), int(done[1])
    pending = session.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.PENDING).scalar() or 0
    avg = total / files if files else None
    return {
        "window_days": days,
        "files": files,
        "downloaded_gb": round(total / 1e9, 2),
        "gb_per_day": round(total / 1e9 / days, 2),
        "avg_file_mb": round(avg / 1e6, 1) if avg else None,
        "pending_jobs": pending,
        "pending_gb_estimate": round(pending * avg / 1e9, 1) if avg else None,
    }
