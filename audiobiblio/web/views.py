"""
views — HTML page routes for the dashboard.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ..db.models import (
    Episode, Work, Series, Program, DownloadJob, CrawlTarget,
    JobStatus, AvailabilityStatus, AssetType,
)
from .deps import get_db

router = APIRouter(tags=["views"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    ep_total = db.query(func.count(Episode.id)).scalar() or 0
    ep_avail = db.query(func.count(Episode.id)).filter(
        Episode.availability_status == AvailabilityStatus.AVAILABLE
    ).scalar() or 0
    j_pending = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.PENDING
    ).scalar() or 0
    j_error = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.ERROR
    ).scalar() or 0
    j_success = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.SUCCESS
    ).scalar() or 0
    t_active = db.query(func.count(CrawlTarget.id)).filter(
        CrawlTarget.active == True
    ).scalar() or 0
    last_crawl = db.query(func.max(CrawlTarget.last_crawled_at)).scalar()

    recent_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode)
    ).order_by(DownloadJob.id.desc()).limit(10).all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "ep_total": ep_total,
        "ep_avail": ep_avail,
        "j_pending": j_pending,
        "j_error": j_error,
        "j_success": j_success,
        "t_active": t_active,
        "last_crawl": last_crawl,
        "recent_jobs": recent_jobs,
    })


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    limit = 50
    offset = (page - 1) * limit
    q = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    )
    if status:
        try:
            q = q.filter(DownloadJob.status == JobStatus(status))
        except ValueError:
            pass
    total = q.count()
    items = q.order_by(DownloadJob.id.desc()).offset(offset).limit(limit).all()
    pages = (total + limit - 1) // limit

    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": items,
        "status_filter": status,
        "page": page,
        "pages": pages,
        "total": total,
        "statuses": [s.value for s in JobStatus],
    })


@router.get("/episodes", response_class=HTMLResponse)
def episodes_page(
    request: Request,
    q: str | None = Query(None),
    availability: str | None = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    limit = 50
    offset = (page - 1) * limit
    query = db.query(Episode).options(
        joinedload(Episode.work).joinedload(Work.series).joinedload(Series.program),
        joinedload(Episode.assets),
    )
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(Episode.title.ilike(pattern), Episode.url.ilike(pattern)))
    if availability:
        try:
            query = query.filter(Episode.availability_status == AvailabilityStatus(availability))
        except ValueError:
            pass
    total = query.count()
    items = query.order_by(Episode.id.desc()).offset(offset).limit(limit).all()
    pages = (total + limit - 1) // limit

    return templates.TemplateResponse("episodes.html", {
        "request": request,
        "episodes": items,
        "search": q or "",
        "availability_filter": availability,
        "page": page,
        "pages": pages,
        "total": total,
        "availabilities": [a.value for a in AvailabilityStatus],
    })


@router.get("/targets", response_class=HTMLResponse)
def targets_page(request: Request, db: Session = Depends(get_db)):
    targets = db.query(CrawlTarget).order_by(CrawlTarget.id).all()
    return templates.TemplateResponse("targets.html", {
        "request": request,
        "targets": targets,
    })


@router.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request):
    return templates.TemplateResponse("ingest.html", {"request": request})


@router.get("/programs", response_class=HTMLResponse)
def programs_page(request: Request, db: Session = Depends(get_db)):
    from collections import defaultdict
    from sqlalchemy import func as sqlfunc

    programs = (
        db.query(Program)
        .options(joinedload(Program.station))
        .order_by(Program.name)
        .all()
    )

    # Episode counts per program
    ep_counts: dict[int, int] = {}
    rows = (
        db.query(Program.id, sqlfunc.count(Episode.id))
        .outerjoin(Series, Series.program_id == Program.id)
        .outerjoin(Work, Work.series_id == Series.id)
        .outerjoin(Episode, Episode.work_id == Work.id)
        .group_by(Program.id)
        .all()
    )
    for prog_id, count in rows:
        ep_counts[prog_id] = count

    # Crawl targets by URL
    prog_urls = [p.url for p in programs if p.url]
    crawl_targets: dict[str, CrawlTarget] = {}
    if prog_urls:
        targets = db.query(CrawlTarget).filter(CrawlTarget.url.in_(prog_urls)).all()
        crawl_targets = {t.url: t for t in targets}

    # Group by station
    by_station: dict[str, dict] = {}
    for prog in programs:
        ct = crawl_targets.get(prog.url) if prog.url else None
        code = prog.station.code
        if code not in by_station:
            by_station[code] = {"code": code, "name": prog.station.name, "programs": []}
        by_station[code]["programs"].append({
            "id": prog.id,
            "name": prog.name,
            "url": prog.url,
            "genre": prog.genre,
            "channel_label": prog.channel_label,
            "episode_count": ep_counts.get(prog.id, 0),
            "crawl_active": ct.active if ct else False,
            "last_crawled": ct.last_crawled_at if ct else prog.last_crawled_at,
        })

    stations = sorted(by_station.values(), key=lambda s: s["name"])

    return templates.TemplateResponse("programs.html", {
        "request": request,
        "stations": stations,
        "total_programs": len(programs),
    })


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    recent = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode)
    ).filter(
        DownloadJob.finished_at.isnot(None)
    ).order_by(DownloadJob.finished_at.desc()).limit(100).all()

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "entries": recent,
    })


# --- htmx partials ---

@router.get("/_partials/stats", response_class=HTMLResponse)
def partial_stats(request: Request, db: Session = Depends(get_db)):
    j_pending = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.PENDING
    ).scalar() or 0
    j_error = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.ERROR
    ).scalar() or 0
    return templates.TemplateResponse("_partials/stats.html", {
        "request": request,
        "j_pending": j_pending,
        "j_error": j_error,
    })


@router.get("/_partials/job_rows", response_class=HTMLResponse)
def partial_job_rows(
    request: Request,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    limit = 50
    offset = (page - 1) * limit
    q = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    )
    if status:
        try:
            q = q.filter(DownloadJob.status == JobStatus(status))
        except ValueError:
            pass
    items = q.order_by(DownloadJob.id.desc()).offset(offset).limit(limit).all()
    return templates.TemplateResponse("_partials/job_rows.html", {
        "request": request,
        "jobs": items,
    })
