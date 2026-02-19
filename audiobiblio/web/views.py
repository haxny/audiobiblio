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
    CatalogEntry, Episode, Work, Series, Program, DownloadJob, CrawlTarget,
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

    # Approval queue
    approval_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work).joinedload(Work.series).joinedload(Series.program)
    ).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).order_by(DownloadJob.id.asc()).limit(50).all()

    # Attach proposed paths for display
    from ..pipelines.library import build_paths_for_episode
    for j in approval_jobs:
        if j.episode:
            try:
                paths = build_paths_for_episode(j.episode, j.episode.work)
                j.proposed_path = str(paths["base_dir"] / f"{paths['stem']}.m4a")
            except Exception:
                j.proposed_path = "?"

    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": items,
        "status_filter": status,
        "page": page,
        "pages": pages,
        "total": total,
        "statuses": [s.value for s in JobStatus],
        "approval_jobs": approval_jobs,
        "approval_count": len(approval_jobs),
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

    # Job stats per program: {program_id: {pending, running, success, error}}
    job_stats: dict[int, dict] = {}
    job_rows = (
        db.query(
            Series.program_id,
            DownloadJob.status,
            sqlfunc.count(DownloadJob.id),
        )
        .select_from(DownloadJob)
        .join(Episode, DownloadJob.episode_id == Episode.id)
        .join(Work, Episode.work_id == Work.id)
        .join(Series, Work.series_id == Series.id)
        .group_by(Series.program_id, DownloadJob.status)
        .all()
    )
    for prog_id, status, count in job_rows:
        if prog_id not in job_stats:
            job_stats[prog_id] = {}
        job_stats[prog_id][status.value] = count

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
        js = job_stats.get(prog.id, {})
        by_station[code]["programs"].append({
            "id": prog.id,
            "name": prog.name,
            "url": prog.url,
            "genre": prog.genre,
            "channel_label": prog.channel_label,
            "episode_count": ep_counts.get(prog.id, 0),
            "crawl_active": ct.active if ct else False,
            "last_crawled": ct.last_crawled_at if ct else prog.last_crawled_at,
            "jobs_pending": js.get("pending", 0),
            "jobs_running": js.get("running", 0),
            "jobs_success": js.get("success", 0),
            "jobs_error": js.get("error", 0),
        })

    stations = sorted(by_station.values(), key=lambda s: s["name"])

    return templates.TemplateResponse("programs.html", {
        "request": request,
        "stations": stations,
        "total_programs": len(programs),
    })


@router.get("/jdownloader", response_class=HTMLResponse)
def jdownloader_page(request: Request):
    return templates.TemplateResponse("jdownloader.html", {"request": request})


@router.get("/catalog", response_class=HTMLResponse)
def catalog_index(request: Request, db: Session = Depends(get_db)):
    """Catalog landing page — lists programs that have catalog entries."""
    programs = (
        db.query(Program)
        .options(joinedload(Program.station))
        .order_by(Program.name)
        .all()
    )
    # Count catalog entries per program
    from sqlalchemy import func as sqlfunc
    counts = dict(
        db.query(CatalogEntry.program_id, sqlfunc.count(CatalogEntry.id))
        .group_by(CatalogEntry.program_id)
        .all()
    )
    return templates.TemplateResponse("catalog.html", {
        "request": request,
        "programs": programs,
        "catalog_counts": counts,
        "active": "catalog",
    })


@router.get("/catalog/{program_id}", response_class=HTMLResponse)
def catalog_detail(
    request: Request,
    program_id: int,
    status: str | None = Query(None),
    folder: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Per-program catalog view with gap report."""
    program = db.query(Program).filter(Program.id == program_id).first()
    if not program:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/catalog")

    from ..pipelines.gaps import gap_report
    report = gap_report(db, program_id)

    # Filter entries by status if requested
    entries = report["entries"]
    if status:
        entries = [e for e in entries if e["status"] == status]

    # Unmatched files (if folder provided)
    unmatched_files: list[dict] = []
    if folder:
        import os, re
        from ..reconcile import scan_folder
        scanned = scan_folder(folder)
        matched_paths = {
            e.local_file for e in db.query(CatalogEntry).filter(
                CatalogEntry.program_id == program_id,
                CatalogEntry.local_file.isnot(None),
            ).all()
        }
        for f in scanned:
            if f["path"] not in matched_paths:
                tag_title = f["title_from_tags"] or ""
                # Skip generic/hash tag titles
                if re.match(r'^[a-f0-9]{32}', tag_title) or tag_title in (
                    "Stopy fakta tajemství", "Stopy, fakta, tajemství",
                ):
                    tag_title = ""

                # Extract date from filename (YYYY-MM-DD or YYYYMMDD)
                fn = f["filename"]
                date_from_filename = ""
                dm = re.search(r'(\d{4})-(\d{2})-(\d{2})', fn)
                if not dm:
                    dm = re.search(r'(?:^|[_ ])(\d{4})(\d{2})(\d{2})(?:[_ \[]|$)', fn)
                if dm:
                    try:
                        from datetime import datetime as dt
                        dt(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
                        date_from_filename = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
                    except ValueError:
                        pass

                # Also check tag date
                tag_date = str(f["tags"].get("date", ""))[:10] if f["tags"].get("date") else ""

                suggested_date = date_from_filename or tag_date

                # File size
                try:
                    size_bytes = os.path.getsize(f["path"])
                    if size_bytes > 1_000_000:
                        size_str = f"{size_bytes / 1_000_000:.1f}M"
                    else:
                        size_str = f"{size_bytes / 1_000:.0f}K"
                except OSError:
                    size_str = "?"

                unmatched_files.append({
                    "path": f["path"],
                    "filename": os.path.basename(f["path"]),
                    "episode_number": f["episode_number"],
                    "tag_title": tag_title,
                    "title_from_filename": f["title_from_filename"],
                    "suggested_date": suggested_date,
                    "date_is_guess": bool(suggested_date),
                    "size": size_str,
                    "tags": {k: str(v)[:80] for k, v in f["tags"].items()
                             if k in ("album", "artist", "tracknumber", "title", "date")},
                })

        # Sort: by suggested_date first (blanks last), then episode_number (blanks last)
        def _sort_key(f):
            d = f["suggested_date"] or "9999-99-99"
            n = f["episode_number"] if f["episode_number"] is not None else 99999
            return (d, n)
        unmatched_files.sort(key=_sort_key)

        # Mark duplicate episode numbers
        from collections import Counter
        ep_counts = Counter(
            f["episode_number"] for f in unmatched_files
            if f["episode_number"] is not None
        )
        dup_eps = {n for n, c in ep_counts.items() if c > 1}
        for f in unmatched_files:
            f["is_dup_epnum"] = f["episode_number"] in dup_eps

    return templates.TemplateResponse("catalog_detail.html", {
        "request": request,
        "program": program,
        "report": report,
        "entries": entries,
        "status_filter": status,
        "active": "catalog",
        "folder": folder or "",
        "unmatched_files": unmatched_files,
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
