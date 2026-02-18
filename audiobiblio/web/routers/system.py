"""
routers/system — Health check, stats, ABS scan trigger.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db.models import (
    Episode, DownloadJob, CrawlTarget, JobStatus, AvailabilityStatus,
)
from ..deps import get_db
from ..schemas import HealthResponse, StatsResponse, TaskResponse
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    return HealthResponse(
        status="ok",
        scheduler_running=scheduler.running if scheduler else False,
    )


@router.get("/stats", response_model=StatsResponse)
def stats(db: Session = Depends(get_db)):
    ep_total = db.query(func.count(Episode.id)).scalar() or 0
    ep_avail = db.query(func.count(Episode.id)).filter(
        Episode.availability_status == AvailabilityStatus.AVAILABLE
    ).scalar() or 0
    ep_gone = db.query(func.count(Episode.id)).filter(
        Episode.availability_status == AvailabilityStatus.GONE
    ).scalar() or 0

    j_total = db.query(func.count(DownloadJob.id)).scalar() or 0
    j_pending = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.PENDING
    ).scalar() or 0
    j_error = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.ERROR
    ).scalar() or 0
    j_success = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.SUCCESS
    ).scalar() or 0

    t_total = db.query(func.count(CrawlTarget.id)).scalar() or 0
    t_active = db.query(func.count(CrawlTarget.id)).filter(
        CrawlTarget.active == True
    ).scalar() or 0

    last_crawl = db.query(func.max(CrawlTarget.last_crawled_at)).scalar()
    last_download = db.query(func.max(DownloadJob.finished_at)).filter(
        DownloadJob.status == JobStatus.SUCCESS
    ).scalar()

    return StatsResponse(
        episodes_total=ep_total,
        episodes_available=ep_avail,
        episodes_gone=ep_gone,
        jobs_total=j_total,
        jobs_pending=j_pending,
        jobs_error=j_error,
        jobs_success=j_success,
        targets_total=t_total,
        targets_active=t_active,
        last_crawl=last_crawl,
        last_download=last_download,
    )


@router.post("/system/abs-scan", response_model=TaskResponse)
def abs_scan():
    from ...abs_client import trigger_library_scan
    task_id = task_tracker.submit("abs_scan", trigger_library_scan)
    return TaskResponse(task_id=task_id, name="abs_scan", status="running")
