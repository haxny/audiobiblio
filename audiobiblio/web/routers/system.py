"""
routers/system — Health check, stats, ABS scan trigger.
"""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    Episode, DownloadJob, CrawlTarget, JobStatus, AvailabilityStatus,
)
from audiobiblio.acquire import downloader, forecast
from ..deps import get_db
from ..schemas import HealthResponse, StatsResponse, TaskResponse, SchedulerStatusResponse, SchedulerJobInfo
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    return HealthResponse(
        status="ok",
        scheduler_running=scheduler.running if scheduler else False,
    )


class IntakeSwitch(BaseModel):
    paused: bool | None = None
    low_priority_paused: bool | None = None


def _intake_state(db: Session) -> dict:
    def pending(cond):
        return (db.query(func.count(DownloadJob.id))
                .join(Episode, Episode.id == DownloadJob.episode_id)
                .filter(DownloadJob.status == JobStatus.PENDING, cond).scalar() or 0)
    return {"paused": downloader.downloads_paused(),
            "low_priority_paused": downloader.low_priority_paused(),
            "pending": {"priority": pending(Episode.priority >= 0),
                        "back_catalog": pending(Episode.priority < 0)},
            "forecast": forecast.download_forecast(db)}


@router.get("/system/intake")
def intake_status(db: Session = Depends(get_db)):
    """Download intake: both pause switches and the pending queue per tier."""
    return _intake_state(db)


@router.post("/system/intake")
def intake_switch(body: IntakeSwitch, db: Session = Depends(get_db)):
    """Flip the pause switches (the same flag files the space watchdog uses)."""
    if body.paused is not None:
        downloader.set_flag(downloader._pause_file(), body.paused)
    if body.low_priority_paused is not None:
        downloader.set_flag(downloader._low_priority_file(), body.low_priority_paused)
    return _intake_state(db)


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


@router.get("/system/scheduler", response_model=SchedulerStatusResponse)
def scheduler_status(request: Request):
    """Return scheduler running state and scheduled job list.

    Guards against scheduler being None (test client or startup race).
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return SchedulerStatusResponse(running=False, jobs=[])

    jobs = [
        SchedulerJobInfo(
            id=job.id,
            next_run_time=job.next_run_time,
        )
        for job in scheduler.get_jobs()
    ]
    return SchedulerStatusResponse(running=bool(scheduler.running), jobs=jobs)


@router.post("/system/abs-scan", response_model=TaskResponse)
def abs_scan():
    from audiobiblio.library.abs_client import trigger_library_scan
    task_id = task_tracker.submit("abs_scan", trigger_library_scan)
    return TaskResponse(task_id=task_id, name="abs_scan", status="running")


def _run_health_check_bg() -> None:
    """Run the daily health check now (own session; digest rules apply)."""
    from audiobiblio.core.db.session import get_session
    from audiobiblio.library.health.job import run_health_check
    run_health_check(get_session())


@router.post("/system/health-check", status_code=202)
def health_check_now(background: BackgroundTasks):
    background.add_task(_run_health_check_bg)
    return {"status": "started"}
