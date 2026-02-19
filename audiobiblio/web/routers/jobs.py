"""
routers/jobs — Download job listing, retry, run.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from ...db.models import DownloadJob, Episode, Work, JobStatus
from ..deps import get_db
from ..schemas import JobResponse, PaginatedJobs, TaskResponse
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _job_to_response(job: DownloadJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        episode_id=job.episode_id,
        episode_title=job.episode.title if job.episode else "",
        work_title=job.episode.work.title if job.episode and job.episode.work else "",
        asset_type=job.asset_type.value,
        status=job.status.value,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("", response_model=PaginatedJobs)
def list_jobs(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    )
    if status:
        try:
            q = q.filter(DownloadJob.status == JobStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

    total = q.count()
    items = q.order_by(DownloadJob.id.desc()).offset(offset).limit(limit).all()

    return PaginatedJobs(
        items=[_job_to_response(j) for j in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    ).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_to_response(job)


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    ).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.ERROR:
        raise HTTPException(400, f"Can only retry ERROR jobs, current: {job.status.value}")
    job.status = JobStatus.PENDING
    job.error = None
    job.started_at = None
    job.finished_at = None
    db.commit()
    return _job_to_response(job)


@router.post("/retry-all-failed")
def retry_all_failed(db: Session = Depends(get_db)):
    count = db.query(DownloadJob).filter(
        DownloadJob.status == JobStatus.ERROR
    ).update({
        DownloadJob.status: JobStatus.PENDING,
        DownloadJob.error: None,
        DownloadJob.started_at: None,
        DownloadJob.finished_at: None,
    })
    db.commit()
    return {"retried": count}


@router.post("/{job_id}/approve", response_model=JobResponse)
def approve_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    ).get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.APPROVAL:
        raise HTTPException(400, f"Can only approve APPROVAL jobs, current: {job.status.value}")
    job.status = JobStatus.PENDING
    db.commit()
    return _job_to_response(job)


@router.post("/approve-all")
def approve_all(db: Session = Depends(get_db)):
    count = db.query(DownloadJob).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).update({DownloadJob.status: JobStatus.PENDING})
    db.commit()
    return {"approved": count}


@router.post("/run", response_model=TaskResponse)
def run_jobs():
    from ...downloader import run_pending_jobs
    task_id = task_tracker.submit("run_jobs", run_pending_jobs, limit=10)
    return TaskResponse(task_id=task_id, name="run_jobs", status="running")
