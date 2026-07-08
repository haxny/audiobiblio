"""
views — HTML page routes for the dashboard.
"""
from __future__ import annotations
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from datetime import datetime

from audiobiblio.core.config import load_config
from audiobiblio.core.db.models import (
    CatalogEntry, Episode, Work, Series, Program, DownloadJob, CrawlTarget,
    JobStatus, AvailabilityStatus, AssetType,
    UpgradeCandidate, UpgradeStatus,
    ImportFinding, MetadataValue,
)
from audiobiblio.library.pipelines.completeness import (
    complete_audio_count, completed_works, count_incomplete_works,
    incomplete_works, work_completeness,
)
from audiobiblio.core.provenance import resolve_field, WORK_FIELDS as _WORK_LEVEL_FIELDS
from audiobiblio.acquire.crawler import target_state
from .deps import get_db

router = APIRouter(tags=["views"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _fmt_duration_ms(ms: int | None) -> str:
    """Format milliseconds as m:ss or h:mm:ss. Returns '?' for None."""
    if ms is None:
        return "?"
    s = ms // 1000
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _query_upgrade_candidates(db: Session) -> list[dict]:
    """Return PENDING_REVIEW + STAGED candidates as plain dicts for the template."""
    candidates = (
        db.query(UpgradeCandidate)
        .options(joinedload(UpgradeCandidate.episode))
        .filter(UpgradeCandidate.status.in_([
            UpgradeStatus.PENDING_REVIEW,
            UpgradeStatus.STAGED,
        ]))
        .order_by(UpgradeCandidate.id.desc())
        .all()
    )

    result: list[dict] = []
    for c in candidates:
        owned_fmt = _fmt_duration_ms(c.owned_duration_ms)
        cand_fmt = _fmt_duration_ms(c.candidate_duration_ms)

        diff_str = ""
        warn_ads = False
        if c.owned_duration_ms is not None and c.candidate_duration_ms is not None:
            diff_ms = c.candidate_duration_ms - c.owned_duration_ms
            diff_s = diff_ms // 1000
            abs_s = abs(diff_s)
            sign = "+" if diff_s >= 0 else "−"
            diff_str = f"{sign}{abs_s // 60}:{abs_s % 60:02d}"
            warn_ads = diff_ms > 0

        result.append({
            "id": c.id,
            "episode_title": c.episode.title if c.episode else str(c.episode_id),
            "owned_fmt": owned_fmt,
            "cand_fmt": cand_fmt,
            "diff_str": diff_str,
            "warn_ads": warn_ads,
            "candidate_url": c.candidate_url,
            "status": c.status.value,
            "staged_path": c.staged_path,
        })

    return result


def _compute_overdue_count(targets: list, now: datetime) -> int:
    """Return the number of targets whose state is 'overdue' at *now*.

    Pure function — accepts any sequence of objects with .active,
    .interval_hours, and .next_crawl_at attributes.  Suitable for unit testing
    without mounting the full views router.
    """
    return sum(1 for t in targets if target_state(t, now) == "overdue")


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

    inbox_count = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).scalar() or 0
    import_count = db.query(func.count(ImportFinding.id)).filter(
        ImportFinding.status == "new"
    ).scalar() or 0
    upgrade_count = db.query(func.count(UpgradeCandidate.id)).filter(
        UpgradeCandidate.status.in_([
            UpgradeStatus.PENDING_REVIEW,
            UpgradeStatus.STAGED,
        ])
    ).scalar() or 0
    running_count = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.RUNNING
    ).scalar() or 0
    gaps_count = count_incomplete_works(db)
    running_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode)
    ).filter(
        DownloadJob.status == JobStatus.RUNNING
    ).order_by(DownloadJob.started_at.desc()).limit(10).all()
    error_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode)
    ).filter(
        DownloadJob.status == JobStatus.ERROR
    ).order_by(DownloadJob.finished_at.desc()).limit(5).all()
    targets_health = db.query(CrawlTarget).order_by(
        CrawlTarget.active.desc(),
        CrawlTarget.next_crawl_at.asc().nullslast(),
    ).limit(20).all()
    now = datetime.utcnow()
    overdue_count = _compute_overdue_count(targets_health, now)
    cfg = load_config()
    try:
        usage = shutil.disk_usage(Path(cfg.library_dir).expanduser())
        disk_free_gb = round(usage.free / 1e9, 1)
    except OSError:
        disk_free_gb = None

    return templates.TemplateResponse(request, "index.html", {
        "ep_total": ep_total,
        "ep_avail": ep_avail,
        "j_pending": j_pending,
        "j_error": j_error,
        "j_success": j_success,
        "t_active": t_active,
        "last_crawl": last_crawl,
        "recent_jobs": recent_jobs,
        "inbox_count": inbox_count,
        "import_count": import_count,
        "upgrade_count": upgrade_count,
        "running_count": running_count,
        "running_jobs": running_jobs,
        "error_jobs": error_jobs,
        "targets_health": targets_health,
        "disk_free_gb": disk_free_gb,
        "overdue_count": overdue_count,
        "gaps_count": gaps_count,
        "target_state": target_state,
        "now": now,
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

    approval_count = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).scalar() or 0

    watch_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    ).filter(
        DownloadJob.status == JobStatus.WATCH
    ).order_by(DownloadJob.id.desc()).limit(50).all()

    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": items,
        "status_filter": status,
        "page": page,
        "pages": pages,
        "total": total,
        "statuses": [s.value for s in JobStatus],
        "approval_count": approval_count,
        "watch_jobs": watch_jobs,
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

    return templates.TemplateResponse(request, "episodes.html", {
        "episodes": items,
        "search": q or "",
        "availability_filter": availability,
        "page": page,
        "pages": pages,
        "total": total,
        "availabilities": [a.value for a in AvailabilityStatus],
    })


# Editable metadata fields shown on the detail page, in display order.
# Routing mirrors PATCH /api/v1/episodes/{id}/metadata (Task 4):
# author + year live on the Work entity, everything else on the Episode.
# _WORK_LEVEL_FIELDS imported from core.provenance as WORK_FIELDS — single source of truth.
_METADATA_FIELDS = ("title", "author", "narrator", "genre", "description", "year")


def _fmt_size(size_bytes: int | None) -> str:
    """Human-readable file size. Returns '?' for None."""
    if size_bytes is None:
        return "?"
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.2f} GB"
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    return f"{size_bytes / 1_000:.0f} kB"


def _episode_metadata_rows(db: Session, ep: Episode) -> list[dict]:
    """One row per editable metadata field for the detail page.

    Each row: field, current (ORM value as str or None), resolved_value,
    resolved_origin (lowercase FieldOrigin name or None), history (all
    MetadataValue observations, newest first).

    Pure-ish (db in, plain data out) so it can be unit-tested without
    mounting the views router — same pattern as _group_approval_jobs.
    """
    work = ep.work
    current_values: dict[str, str | None] = {
        "title": ep.title,
        "description": ep.summary,
        "author": work.author if work else None,
        "year": str(work.year) if work and work.year is not None else None,
        "narrator": None,   # provenance-only — no ORM column
        "genre": None,      # provenance-only — no ORM column
    }

    rows: list[dict] = []
    for field in _METADATA_FIELDS:
        if field in _WORK_LEVEL_FIELDS:
            entity_type, entity_id = "work", ep.work_id
        else:
            entity_type, entity_id = "episode", ep.id

        candidates = (
            db.query(MetadataValue)
            .filter_by(entity_type=entity_type, entity_id=entity_id, field=field)
            .order_by(MetadataValue.observed_at.desc())
            .all()
        )
        winner = resolve_field(candidates)
        rows.append({
            "field": field,
            "current": current_values[field],
            "resolved_value": winner.value if winner else None,
            "resolved_origin": winner.origin.name.lower() if winner else None,
            "history": [
                {
                    "value": c.value,
                    "origin": c.origin.name.lower(),
                    "source": c.source,
                    "observed_at": c.observed_at,
                }
                for c in candidates
            ],
        })
    return rows


def _episode_asset_rows(ep: Episode) -> list[dict]:
    """Assets as plain dicts with a per-file exists check for the files table."""
    rows: list[dict] = []
    for a in ep.assets:
        exists = bool(a.file_path) and Path(a.file_path).is_file()
        rows.append({
            "type": a.type.value,
            "status": a.status.value,
            "file_path": a.file_path,
            "exists": exists,
            "size_fmt": _fmt_size(a.size_bytes),
            "bitrate_fmt": f"{a.bitrate // 1000} kbps" if a.bitrate else "?",
        })
    return rows


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail_page(
    request: Request,
    episode_id: int,
    db: Session = Depends(get_db),
):
    ep = (
        db.query(Episode)
        .options(
            joinedload(Episode.work).joinedload(Work.series).joinedload(Series.program),
            joinedload(Episode.assets),
            joinedload(Episode.jobs),
        )
        .filter(Episode.id == episode_id)
        .first()
    )
    if ep is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/episodes")

    work = ep.work
    series = work.series if work else None
    program = series.program if series else None

    assets = _episode_asset_rows(ep)
    has_audio = any(
        a["type"] == AssetType.AUDIO.value and a["status"] == "complete" and a["exists"]
        for a in assets
    )

    # Work is finalizable when expected_total is set and reached (Phase 5 Task 7).
    work_complete = bool(
        work
        and work.expected_total is not None
        and complete_audio_count(db, work.id) >= work.expected_total
    )

    return templates.TemplateResponse(request, "episode_detail.html", {
        "episode": ep,
        "work": work,
        "series": series,
        "program": program,
        "assets": assets,
        "has_audio": has_audio,
        "work_complete": work_complete,
        "metadata_rows": _episode_metadata_rows(db, ep),
        "jobs": sorted(ep.jobs, key=lambda j: j.id, reverse=True),
        "duration_fmt": _fmt_duration_ms(ep.duration_ms),
        "active": "episodes",
    })


@router.get("/targets", response_class=HTMLResponse)
def targets_page(request: Request, db: Session = Depends(get_db)):
    targets = db.query(CrawlTarget).order_by(CrawlTarget.id).all()
    return templates.TemplateResponse(request, "targets.html", {
        "targets": targets,
    })


@router.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request):
    return templates.TemplateResponse(request, "ingest.html")


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

    return templates.TemplateResponse(request, "programs.html", {
        "stations": stations,
        "total_programs": len(programs),
    })


@router.get("/jdownloader", response_class=HTMLResponse)
def jdownloader_page(request: Request):
    return templates.TemplateResponse(request, "jdownloader.html")


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
    return templates.TemplateResponse(request, "catalog.html", {
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

    from audiobiblio.library.pipelines.gaps import gap_report
    report = gap_report(db, program_id)

    # Filter entries by status if requested
    entries = report["entries"]
    if status:
        entries = [e for e in entries if e["status"] == status]

    # Unmatched files (if folder provided)
    unmatched_files: list[dict] = []
    if folder:
        import os, re
        from audiobiblio.reconcile import scan_folder
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

    return templates.TemplateResponse(request, "catalog_detail.html", {
        "program": program,
        "report": report,
        "entries": entries,
        "status_filter": status,
        "active": "catalog",
        "folder": folder or "",
        "unmatched_files": unmatched_files,
    })


def _group_approval_jobs(db: Session) -> tuple[list[dict], int]:
    """Return (groups, total_jobs) for all APPROVAL-status jobs.

    Groups are ordered by program_name.  Each group dict has keys:
      program_name  — str
      episodes      — list of episode dicts, each with:
          id            int
          title         str
          url           str | None
          proposed_path str  (from build_paths_for_episode; "?" on error)
          job_ids       list[int]   — all APPROVAL job IDs for this episode
          asset_types   list[str]   — matching asset type values

    proposed_path is based on the episode's AUDIO job path when one is present
    in the APPROVAL set; otherwise it falls back to the first job's episode
    path.  Either way it is computed once per episode, not per job.

    This is a pure-ish function (takes db, returns plain data) so it can be
    unit-tested without mounting the full views router.
    """
    from audiobiblio.library.pipelines.library import build_paths_for_episode

    jobs = (
        db.query(DownloadJob)
        .options(
            joinedload(DownloadJob.episode)
            .joinedload(Episode.work)
            .joinedload(Work.series)
            .joinedload(Series.program)
        )
        .filter(DownloadJob.status == JobStatus.APPROVAL)
        .order_by(DownloadJob.id.asc())
        .all()
    )

    # Group by episode_id → one episode dict per episode
    episodes_map: dict[int, dict] = {}
    for j in jobs:
        ep = j.episode
        ep_id = j.episode_id
        if ep_id not in episodes_map:
            try:
                paths = build_paths_for_episode(ep, ep.work) if ep else None
                proposed = (
                    str(paths["base_dir"] / f"{paths['stem']}.m4a")
                    if paths else "?"
                )
            except Exception:
                proposed = "?"
            work = getattr(ep, "work", None) if ep else None
            series = getattr(work, "series", None) if work else None
            program = getattr(series, "program", None) if series else None
            program_name = getattr(program, "name", None) or "Unknown"
            episodes_map[ep_id] = {
                "id": ep_id,
                "title": ep.title if ep else str(ep_id),
                "url": ep.url if ep else None,
                "proposed_path": proposed,
                "job_ids": [],
                "asset_types": [],
                "gap_fill": False,
                "_program_name": program_name,
            }
        episodes_map[ep_id]["job_ids"].append(j.id)
        episodes_map[ep_id]["asset_types"].append(j.asset_type.value)
        if j.reason and "gap-fill" in j.reason:
            episodes_map[ep_id]["gap_fill"] = True

    # Group episodes by program_name
    groups_map: dict[str, list] = {}
    for ep_data in episodes_map.values():
        program_name = ep_data.pop("_program_name")
        groups_map.setdefault(program_name, []).append(ep_data)

    groups = [
        {"program_name": name, "episodes": ep_list}
        for name, ep_list in sorted(groups_map.items())
    ]
    return groups, len(jobs)


def _query_gaps(db: Session, limit: int = 100) -> list[dict]:
    """Return gaps data as plain dicts for the /gaps template.

    Each row has: work_id, work_title, program_name, have, expected,
    missing_numbers (list[int] or None), first_episode_id (int or None).

    Pure-ish function (takes db, returns plain data) — testable without
    mounting the views router.

    Optimized: Series and Program are eager-loaded from incomplete_works;
    first-episode IDs are batched in a single query.
    """
    pairs = incomplete_works(db, limit=limit)
    if not pairs:
        return []

    # Batch lookup: fetch first episode for all works in one query
    work_ids = [work.id for work, _ in pairs]
    first_eps_raw = (
        db.query(Episode.work_id, func.min(Episode.id))
        .filter(Episode.work_id.in_(work_ids))
        .group_by(Episode.work_id)
        .all()
    )
    first_eps_map = {work_id: ep_id for work_id, ep_id in first_eps_raw}

    result: list[dict] = []
    for work, have in pairs:
        # Series and Program are already eager-loaded by incomplete_works
        program = work.series.program if work.series else None

        # work_completeness is needed only for missing_numbers ('have' comes
        # from incomplete_works); every row here has expected_total set, so
        # the numbering heuristic can always potentially apply.
        comp = work_completeness(db, work)

        result.append({
            "work_id": work.id,
            "work_title": work.title,
            "program_name": program.name if program else "—",
            "have": have,
            "expected": work.expected_total,
            "missing_numbers": comp.missing_numbers,
            "first_episode_id": first_eps_map.get(work.id),
        })
    return result


def _query_completed(db: Session, limit: int = 100) -> list[dict]:
    """Return works ready to finalize (have >= expected_total) as plain dicts.

    Each row has: work_id, work_title, program_name, have, expected.
    Same pure-data pattern as _query_gaps.
    """
    pairs = completed_works(db, limit=limit)
    result: list[dict] = []
    for work, have in pairs:
        program = work.series.program if work.series else None
        result.append({
            "work_id": work.id,
            "work_title": work.title,
            "program_name": program.name if program else "—",
            "have": have,
            "expected": work.expected_total,
        })
    return result


@router.get("/segmentation", response_class=HTMLResponse)
def segmentation_page(request: Request, db: Session = Depends(get_db)):
    """Segmentation review page — propose and apply per program."""
    programs = db.query(Program).options(joinedload(Program.station)).order_by(Program.name).all()
    program_rows = [
        {"id": p.id, "name": p.name, "station": p.station.name}
        for p in programs
    ]
    return templates.TemplateResponse(request, "segmentation.html", {
        "programs": program_rows,
        "active": "segmentation",
    })


@router.get("/gaps", response_class=HTMLResponse)
def gaps_page(request: Request, db: Session = Depends(get_db)):
    """Gap report — works with expected_total set and have < expected,
    plus complete works eligible for finalization."""
    rows = _query_gaps(db)
    completed = _query_completed(db)
    return templates.TemplateResponse(request, "gaps.html", {
        "rows": rows,
        "completed": completed,
        "active": "gaps",
    })


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, db: Session = Depends(get_db)):
    groups, total = _group_approval_jobs(db)
    candidates = _query_upgrade_candidates(db)
    return templates.TemplateResponse(request, "inbox.html", {
        "groups": groups,
        "total": total,
        "candidates": candidates,
        "active": "inbox",
    })


@router.get("/dedupe", response_class=HTMLResponse)
def dedupe_page(
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    from audiobiblio.dedupe.clusters import find_duplicate_clusters

    clusters = find_duplicate_clusters(db, limit=limit)
    return templates.TemplateResponse(request, "dedupe.html", {
        "clusters": clusters,
        "limit": limit,
        "active": "dedupe",
    })


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import func as sqlfunc

    cfg = load_config()
    bucket_counts_raw = (
        db.query(ImportFinding.bucket, sqlfunc.count(ImportFinding.id))
        .filter(ImportFinding.status == "new")
        .group_by(ImportFinding.bucket)
        .all()
    )
    bucket_counts = {b.value: c for b, c in bucket_counts_raw}
    return templates.TemplateResponse(request, "import.html", {
        "bucket_counts": bucket_counts,
        "inbox_dirs": cfg.inbox_dirs,
        "active": "import",
    })


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    recent = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode)
    ).filter(
        DownloadJob.finished_at.isnot(None)
    ).order_by(DownloadJob.finished_at.desc()).limit(100).all()

    return templates.TemplateResponse(request, "logs.html", {
        "entries": recent,
    })


# --- htmx partials ---

@router.get("/_partials/inbox_badge", response_class=HTMLResponse)
def partial_inbox_badge(request: Request, db: Session = Depends(get_db)):
    count = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).scalar() or 0
    return templates.TemplateResponse(request, "_partials/inbox_badge.html", {
        "count": count,
    })


@router.get("/_partials/stats", response_class=HTMLResponse)
def partial_stats(request: Request, db: Session = Depends(get_db)):
    j_pending = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.PENDING
    ).scalar() or 0
    j_error = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.ERROR
    ).scalar() or 0
    return templates.TemplateResponse(request, "_partials/stats.html", {
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
    return templates.TemplateResponse(request, "_partials/job_rows.html", {
        "jobs": items,
    })
