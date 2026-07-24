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
from unidecode import unidecode

from datetime import datetime

from audiobiblio.core.config import load_config
from audiobiblio.core.time import utcnow
from audiobiblio.core.db.models import (
    CatalogEntry, Episode, Work, Series, Program, Station, DownloadJob, CrawlTarget, Asset,
    JobStatus, AvailabilityStatus, AssetType, AssetStatus,
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
    # LIMIT-20: overdue_count is derived from this slice, not all targets —
    # intentional; the dashboard counter reflects the most-urgent 20 targets.
    targets_health = db.query(CrawlTarget).order_by(
        CrawlTarget.active.desc(),
        CrawlTarget.next_crawl_at.asc().nullslast(),
    ).limit(20).all()
    now = utcnow()
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


def _query_job_groups(db: Session, status: str | None, page: int, limit: int):
    """Group download jobs by EPISODE for the human-first Downloads page.

    One row per episode; per asset type only the LATEST job counts (older
    attempts are history — the episode detail page shows them all). A status
    filter selects episodes whose latest job of ANY asset has that status.

    Returns (groups, total_episodes, total_jobs, pages) where each group is
    {"episode": Episode, "assets": {asset_value: DownloadJob}, "latest": DownloadJob}.
    """
    status_enum = None
    if status:
        try:
            status_enum = JobStatus(status)
        except ValueError:
            pass

    total_jobs = db.query(func.count(DownloadJob.id)).scalar() or 0

    latest_ids = [
        row[0]
        for row in db.query(func.max(DownloadJob.id))
        .filter(DownloadJob.episode_id.isnot(None))
        .group_by(DownloadJob.episode_id, DownloadJob.asset_type)
        .all()
    ]
    jobs = (
        db.query(DownloadJob).filter(DownloadJob.id.in_(latest_ids)).all()
        if latest_ids else []
    )

    by_episode: dict[int, dict] = {}
    for j in jobs:
        g = by_episode.setdefault(j.episode_id, {"assets": {}, "max_id": 0})
        g["assets"][j.asset_type.value] = j
        g["max_id"] = max(g["max_id"], j.id)

    if status_enum is not None:
        by_episode = {
            ep_id: g for ep_id, g in by_episode.items()
            if any(j.status == status_enum for j in g["assets"].values())
        }

    ordered = sorted(by_episode.items(), key=lambda kv: kv[1]["max_id"], reverse=True)
    total_eps = len(ordered)
    pages = max(1, (total_eps + limit - 1) // limit)
    page_items = ordered[(page - 1) * limit: page * limit]

    ep_ids = [ep_id for ep_id, _ in page_items]
    eps = {
        e.id: e
        for e in db.query(Episode).options(joinedload(Episode.work))
        .filter(Episode.id.in_(ep_ids)).all()
    } if ep_ids else {}

    groups = []
    for ep_id, g in page_items:
        ep = eps.get(ep_id)
        if ep is None:
            continue
        latest = max(g["assets"].values(), key=lambda j: j.id)
        groups.append({"episode": ep, "assets": g["assets"], "latest": latest})
    return groups, total_eps, total_jobs, pages


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    limit = 50
    groups, total, total_jobs, pages = _query_job_groups(db, status, page, limit)

    approval_count = db.query(func.count(DownloadJob.id)).filter(
        DownloadJob.status == JobStatus.APPROVAL
    ).scalar() or 0

    watch_jobs = db.query(DownloadJob).options(
        joinedload(DownloadJob.episode).joinedload(Episode.work)
    ).filter(
        DownloadJob.status == JobStatus.WATCH
    ).order_by(DownloadJob.id.desc()).limit(50).all()

    return templates.TemplateResponse(request, "jobs.html", {
        "groups": groups,
        "status_filter": status,
        "page": page,
        "pages": pages,
        "total": total,
        "total_jobs": total_jobs,
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
    # Human order: parts of one book together and in reading order —
    # newest works first, then part number (internal row ids mean nothing).
    items = (
        query.order_by(
            Episode.work_id.desc(),
            Episode.episode_number.asc().nulls_last(),
            Episode.id.asc(),
        )
        .offset(offset).limit(limit).all()
    )
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


@router.get("/programs/{program_id}", response_class=HTMLResponse)
def program_detail_page(request: Request, program_id: int, db: Session = Depends(get_db)):
    """Program detail — every indexed episode with AIR DATE, availability
    and annotation (the SFT reconstruction view: hundreds of aired
    episodes, downloadable or GONE-awaiting-re-air)."""
    from fastapi import HTTPException

    program = (
        db.query(Program)
        .options(joinedload(Program.station))
        .filter(Program.id == program_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found")

    episodes = (
        db.query(Episode)
        .join(Work, Episode.work_id == Work.id)
        .join(Series, Work.series_id == Series.id)
        .filter(Series.program_id == program_id)
        .options(joinedload(Episode.assets), joinedload(Episode.work))
        .order_by(Episode.published_at.desc().nulls_last(), Episode.id.desc())
        .all()
    )
    # Group by WORK: a 10-part book is ONE row ("je tam 10× každé, přitom
    # je to logicky jedna vícedílná kniha"); single-episode works (SFT
    # documentaries) render identically to the old per-episode view.
    by_work: dict[int, dict] = {}
    n_complete = n_gone = 0
    for ep in episodes:
        audio = next((a for a in ep.assets if a.type == AssetType.AUDIO), None)
        status = audio.status.value if audio else None
        gone = ep.availability_status == AvailabilityStatus.GONE
        if status == "complete":
            n_complete += 1
        elif gone:
            n_gone += 1
        g = by_work.setdefault(ep.work_id, {
            "work_id": ep.work_id,
            "title": ep.work.title if ep.work else ep.title,
            "author": ep.work.author if ep.work else None,
            "first_ep_id": ep.id,
            "parts": 0, "parts_complete": 0, "any_gone": False,
            "aired_min": None, "aired_max": None,
            "perex": (ep.summary or "")[:160],
        })
        g["parts"] += 1
        if status == "complete":
            g["parts_complete"] += 1
        if gone:
            g["any_gone"] = True
        if ep.published_at:
            if g["aired_min"] is None or ep.published_at < g["aired_min"]:
                g["aired_min"] = ep.published_at
            if g["aired_max"] is None or ep.published_at > g["aired_max"]:
                g["aired_max"] = ep.published_at
        if not g["perex"] and ep.summary:
            g["perex"] = ep.summary[:160]

    rows = []
    for g in by_work.values():
        lo, hi = g.pop("aired_min"), g.pop("aired_max")
        if lo and hi and lo != hi:
            g["aired"] = f"{lo.strftime('%d.%m.%Y')} – {hi.strftime('%d.%m.%Y')}"
        elif lo:
            g["aired"] = lo.strftime("%d.%m.%Y")
        else:
            g["aired"] = ""
        rows.append(g)
    rows.sort(key=lambda r: r["aired"] == "" and "0" or r["aired"][-4:] + r["aired"][3:5] + r["aired"][:2], reverse=True)

    return templates.TemplateResponse(request, "program_detail.html", {
        "program": program,
        "station_code": program.station.code if program.station else "",
        "rows": rows,
        "n_complete": n_complete,
        "n_gone": n_gone,
        "n_episodes": len(episodes),
        "active": "programs",
    })


@router.get("/library", response_class=HTMLResponse)
def library_page(request: Request, db: Session = Depends(get_db)):
    """Library = list of BOOKS (works), not episodes ("pod menu Library bych
    předpokládal seznam knih"). Client-side eliminative filter; episodes
    stay reachable via /episodes and each book's detail."""
    works = (
        db.query(Work)
        .options(joinedload(Work.series).joinedload(Series.program).joinedload(Program.station))
        .all()
    )
    # complete-audio counts per work in two grouped queries (no N+1)
    totals = dict(
        db.query(Episode.work_id, func.count(Episode.id))
        .group_by(Episode.work_id).all()
    )
    completes = dict(
        db.query(Episode.work_id, func.count(Asset.id))
        .join(Asset, Asset.episode_id == Episode.id)
        .filter(Asset.type == AssetType.AUDIO, Asset.status == AssetStatus.COMPLETE)
        .group_by(Episode.work_id).all()
    )
    rows = []
    for w in works:
        program = w.series.program if w.series else None
        total = totals.get(w.id, 0)
        complete = completes.get(w.id, 0)
        expected = w.expected_total
        rows.append({
            "id": w.id,
            "title": w.title,
            "author": w.author,
            "year": w.year,
            "program": (
                f"{program.name} ({program.station.code})"
                if program and program.station else (program.name if program else "")
            ),
            "total": total,
            "complete": complete,
            "expected": expected,
            "is_complete": (
                complete >= expected if expected else (total > 0 and complete == total)
            ),
        })
    rows.sort(key=lambda r: r["id"], reverse=True)
    return templates.TemplateResponse(request, "library.html", {
        "rows": rows,
        "active": "library",
    })


@router.get("/works/{work_id}", response_class=HTMLResponse)
def work_detail_page(request: Request, work_id: int, db: Session = Depends(get_db)):
    """Work (book) detail — the one page per book: parts in reading order,
    per-part audio status, inline player, completeness and finalize."""
    from fastapi import HTTPException

    work = (
        db.query(Work)
        .options(joinedload(Work.series).joinedload(Series.program).joinedload(Program.station))
        .filter(Work.id == work_id)
        .first()
    )
    if work is None:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    episodes = (
        db.query(Episode)
        .options(joinedload(Episode.assets))
        .filter(Episode.work_id == work_id)
        .order_by(Episode.episode_number.asc().nulls_last(), Episode.id.asc())
        .all()
    )

    # Episodes with a SECOND version awaiting a human decision (ad rule /
    # curated-vs-radio pairs) — surfaced as a "2 verze" badge per part.
    ep_ids = [e.id for e in episodes]
    pending_pairs = {
        uc.episode_id: uc.note or "druhá verze čeká na rozhodnutí"
        for uc in db.query(UpgradeCandidate)
        .filter(
            UpgradeCandidate.episode_id.in_(ep_ids),
            UpgradeCandidate.status == UpgradeStatus.PENDING_REVIEW,
        )
        .all()
    } if ep_ids else {}

    rows = []
    complete = 0
    for ep in episodes:
        audio = next((a for a in ep.assets if a.type == AssetType.AUDIO), None)
        status = audio.status.value if audio else None
        if status == "complete":
            complete += 1
        rows.append({
            "id": ep.id,
            "number": ep.episode_number,
            "title": ep.title,
            "audio_status": status,
            "playable": status == "complete",
            "duration": _fmt_duration_ms(ep.duration_ms),
            "file_path": audio.file_path if audio else None,
            "pending_pair": pending_pairs.get(ep.id),
        })

    series = work.series
    program = series.program if series else None

    # Book-level metadata for the edit card: author/year/publisher from the
    # Work (+provenance); narrator/genre/description resolved from the first
    # episode as the book's representative (editing fans out to all parts).
    def _resolved(entity_type: str, entity_id: int | None, field: str) -> str | None:
        if entity_id is None:
            return None
        candidates = (
            db.query(MetadataValue)
            .filter_by(entity_type=entity_type, entity_id=entity_id, field=field)
            .all()
        )
        winner = resolve_field(candidates)
        return winner.value if winner else None

    first_ep = episodes[0] if episodes else None
    first_ep_id = first_ep.id if first_ep else None
    book_meta = {
        "author": work.author,
        "year": work.year,
        # full broadcast/edition date when known (TDRC/©day accept YYYY,
        # YYYY-MM, YYYY-MM-DD); falls back to the plain year
        "date": _resolved("work", work.id, "date") or (str(work.year) if work.year else None),
        "subtitle": _resolved("work", work.id, "subtitle"),
        # order within an ongoing cycle (SFT c. 62 etc.) — collection programs
        "series_number": _resolved("work", work.id, "series_number"),
        "publisher": _resolved("work", work.id, "publisher"),
        "translator": _resolved("work", work.id, "translator"),
        "final_path": _resolved("work", work.id, "final_path"),
        # canonical url tag (TXXX:www / freeform www); older adoptions
        # recorded it as "source_url"
        "www": (_resolved("work", work.id, "www")
                or _resolved("work", work.id, "source_url")),
        "narrator": _resolved("episode", first_ep_id, "narrator"),
        "genre": _resolved("episode", first_ep_id, "genre"),
        # Book description: work-level winner first (full article text saved
        # on the Work), then the first part's, then its summary (ingest stores
        # the full original block there — diacritics allowed).
        "description": (
            _resolved("work", work.id, "description")
            or _resolved("episode", first_ep_id, "description")
            or (first_ep.summary if first_ep else None)
        ),
    }

    # Where the audio actually sits on disk — the user must be able to tell
    # a working-library download from a book already shelved in the curated
    # structure (and indexed by Audiobookshelf).
    from pathlib import Path as _P
    _audio_paths = [
        a.file_path
        for a in db.query(Asset).join(Episode)
        .filter(Episode.work_id == work.id, Asset.type == AssetType.AUDIO,
                Asset.status == AssetStatus.COMPLETE,
                Asset.file_path.isnot(None)).all()
    ]
    _share = {"/media/fiction": "eBOOKs.fiction", "/media/nonfiction": "eBOOKs.nonfiction",
              "/media/audiobooks": "eBOOKs/audiobooks (pracovní)"}
    locations = []
    for d in sorted({str(_P(p).parent) for p in _audio_paths}):
        label = d
        for pref, share in _share.items():
            if d.startswith(pref):
                label = share + d[len(pref):]
                break
        locations.append({
            "path": label,
            "curated": bool(book_meta["final_path"]) and d == book_meta["final_path"],
        })

    # collection programs (SFT, Historie zlocinu, ...) — a single-episode work
    # is an EPISODE of an ongoing cycle, not a book; the template renders the
    # breadcrumb and header accordingly.
    from audiobiblio.library.pipelines.auto_finalize import DESTINATIONS as _DEST, _norm as _dnorm
    _prog_cfg = _DEST.get(_dnorm(program.name)) if program else None
    is_collection = bool(_prog_cfg and _prog_cfg[1] == "collection") or (
        _prog_cfg is None and len(rows) == 1)

    # cover candidates: every cover_url observation = one gallery item
    cover_candidates = [
        {"url": r.value, "source": r.source, "origin": r.origin.value}
        for r in db.query(MetadataValue)
        .filter_by(entity_type="work", entity_id=work.id, field="cover_url")
        .order_by(MetadataValue.id.desc()).all()
        if r.value and r.value.startswith("http")
    ]

    return templates.TemplateResponse(request, "work_detail.html", {
        "is_collection": is_collection,
        "cover_candidates": cover_candidates,
        "work": work,
        "source_url": next((e.url for e in episodes if e.url), None),
        "series_name": series.name if series else None,
        "program_label": (
            f"{program.name} ({program.station.code})"
            if program and program.station else (program.name if program else None)
        ),
        "rows": rows,
        "complete": complete,
        "total": len(rows),
        "book_meta": book_meta,
        "locations": locations,
        "active": "episodes",
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

    # A second version awaiting a decision (ad rule / curated-vs-radio pair):
    # rendered as a comparison card with its own player.
    pending_pair = (
        db.query(UpgradeCandidate)
        .filter(
            UpgradeCandidate.episode_id == episode_id,
            UpgradeCandidate.status == UpgradeStatus.PENDING_REVIEW,
        )
        .first()
    )
    pair_row = None
    if pending_pair is not None:
        staged = Path(pending_pair.staged_path) if pending_pair.staged_path else None
        owned_audio = next(
            (a for a in ep.assets
             if a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE),
            None,
        )
        pair_row = {
            "id": pending_pair.id,
            "note": pending_pair.note,
            "owned_duration": _fmt_duration_ms(pending_pair.owned_duration_ms),
            "candidate_duration": _fmt_duration_ms(pending_pair.candidate_duration_ms),
            "playable": bool(staged and staged.is_file()),
            "staged_path": pending_pair.staged_path,
            "owned_path": owned_audio.file_path if owned_audio else None,
        }

    return templates.TemplateResponse(request, "episode_detail.html", {
        "pending_pair": pair_row,
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
    targets = (db.query(CrawlTarget)
               .order_by(CrawlTarget.active.desc(), CrawlTarget.name, CrawlTarget.url)
               .all())
    return templates.TemplateResponse(request, "targets.html", {
        "targets": targets,
    })


@router.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request):
    return templates.TemplateResponse(request, "ingest.html")


@router.get("/programs", response_class=HTMLResponse)
def programs_page(request: Request, db: Session = Depends(get_db)):
    from collections import defaultdict


    programs = (
        db.query(Program)
        .options(joinedload(Program.station))
        .order_by(Program.name)
        .all()
    )

    # Episode counts per program
    ep_counts: dict[int, int] = {}
    rows = (
        db.query(Program.id, func.count(Episode.id))
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
            func.count(DownloadJob.id),
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

    # Crawl targets by URL — a program url may be EITHER side of a
    # dual-source pair (rozhlas.cz ⇄ mujrozhlas.cz), so index both sides.
    crawl_targets: dict[str, CrawlTarget] = {}
    for t in db.query(CrawlTarget).all():
        crawl_targets[t.url.rstrip("/")] = t
        if t.paired_url:
            crawl_targets[t.paired_url.rstrip("/")] = t

    # Group by station
    by_station: dict[str, dict] = {}
    for prog in programs:
        ct = crawl_targets.get(prog.url.rstrip("/")) if prog.url else None
        pair_url = None
        if ct:
            # the OTHER side of the pair relative to the program's own url
            own = (prog.url or "").rstrip("/")
            pair_url = ct.paired_url if ct.url.rstrip("/") == own else ct.url
        code = prog.station.code
        if code not in by_station:
            by_station[code] = {"code": code, "name": prog.station.name, "programs": []}
        js = job_stats.get(prog.id, {})
        by_station[code]["programs"].append({
            "id": prog.id,
            "name": prog.name,
            "url": prog.url,
            "pair_url": pair_url,
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

    counts = dict(
        db.query(CatalogEntry.program_id, func.count(CatalogEntry.id))
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
        .join(DownloadJob.episode)  # needed for ORDER BY episode priority
        .filter(DownloadJob.status == JobStatus.APPROVAL)
        .order_by(Episode.priority.desc(), Episode.id.asc())
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


_SEARCH_LIMIT = 50

_EMPTY_SEARCH: dict = {
    "works": [], "episodes": [], "programs": [],
    "works_total": 0, "episodes_total": 0, "programs_total": 0,
}


def _search_norm(s: str | None) -> str:
    """Normalize for search: strip diacritics (unidecode) + lowercase."""
    if not s:
        return ""
    return unidecode(s).lower()


def _query_search(db: Session, q: str, limit: int = _SEARCH_LIMIT) -> dict:
    """Global search across works (title, author), episodes (title, summary)
    and programs (name).

    Case- and diacritics-insensitive: query and stored values are both
    normalized with unidecode + lower before substring matching.  SQL LIKE
    alone can't strip diacritics from stored values ('hasek' must find
    'Hašek'), so matching runs in Python over a narrow column scan.

    Returns plain data for the template: works / episodes / programs lists
    (each capped at `limit`) plus uncapped *_total counts.  Pure-ish function
    (takes db, returns dicts) — testable without mounting the views router.
    """
    needle = _search_norm(q.strip())
    if not needle:
        return dict(_EMPTY_SEARCH)

    # --- works: title or author -------------------------------------------
    work_rows = [
        {"work_id": wid, "title": title, "author": author}
        for wid, title, author in db.query(Work.id, Work.title, Work.author)
        if needle in _search_norm(title) or needle in _search_norm(author)
    ]
    works = work_rows[:limit]

    # Batch lookup: first episode per matched work (same pattern as _query_gaps)
    if works:
        work_ids = [w["work_id"] for w in works]
        first_eps_map = dict(
            db.query(Episode.work_id, func.min(Episode.id))
            .filter(Episode.work_id.in_(work_ids))
            .group_by(Episode.work_id)
            .all()
        )
        shelved_ids = {
            r.entity_id for r in db.query(MetadataValue)
            .filter(MetadataValue.entity_type == "work",
                    MetadataValue.field == "final_path",
                    MetadataValue.entity_id.in_(work_ids),
                    MetadataValue.value.isnot(None)).all()
        }
        works = [
            {**w, "first_episode_id": first_eps_map.get(w["work_id"]),
             "shelved": w["work_id"] in shelved_ids}
            for w in works
        ]

    # --- episodes: title or summary ----------------------------------------
    episode_rows = [
        {"episode_id": eid, "title": title, "work_id": wid,
         "episode_number": num, "published_at": pub,
         "gone": avail == AvailabilityStatus.GONE}
        for eid, title, summary, wid, num, pub, avail in db.query(
            Episode.id, Episode.title, Episode.summary, Episode.work_id,
            Episode.episode_number, Episode.published_at,
            Episode.availability_status,
        )
        if needle in _search_norm(title) or needle in _search_norm(summary)
    ]
    # serial parts share one title — group by work, order by part number so
    # the hits read as a book's tracklist, not N identical rows
    episode_rows.sort(key=lambda e: (e["work_id"] or 0, e["episode_number"] or 0))
    episodes = episode_rows[:limit]

    # Batch lookup: work titles for the shown episodes only
    if episodes:
        ep_work_ids = {e["work_id"] for e in episodes if e["work_id"] is not None}
        work_titles = dict(
            db.query(Work.id, Work.title).filter(Work.id.in_(ep_work_ids)).all()
        ) if ep_work_ids else {}
        episodes = [
            {**e, "work_title": work_titles.get(e["work_id"], "—")}
            for e in episodes
        ]

    # --- programs: name -----------------------------------------------------
    program_rows = [
        {"program_id": pid, "name": name}
        for pid, name in db.query(Program.id, Program.name)
        if needle in _search_norm(name)
    ]

    return {
        "works": works,
        "episodes": episodes,
        "programs": program_rows[:limit],
        "works_total": len(work_rows),
        "episodes_total": len(episode_rows),
        "programs_total": len(program_rows),
    }


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    """Global search results — works, episodes and programs."""
    query = q.strip()
    results = _query_search(db, query) if query else dict(_EMPTY_SEARCH)
    return templates.TemplateResponse(request, "search.html", {
        "q": query,
        "limit": _SEARCH_LIMIT,
        **results,
    })


@router.get("/segmentation", response_class=HTMLResponse)
def segmentation_page(request: Request, db: Session = Depends(get_db)):
    """Segmentation review page — propose and apply per program.

    Only programs that actually have episodes are offered (the catalog
    holds ~100 seeded programs; segmentation is meaningless without
    episodes). Display convention: "Pořad (kanál)" using Station.code.
    """
    rows = (
        db.query(Program, Station.code, func.count(Episode.id).label("n"))
        .join(Station, Program.station_id == Station.id)
        .join(Series, Series.program_id == Program.id)
        .join(Work, Work.series_id == Series.id)
        .join(Episode, Episode.work_id == Work.id)
        .group_by(Program.id, Station.code)
        .order_by(Program.name)
        .all()
    )
    program_rows = [
        {
            "id": p.id,
            "name": p.name,
            "code": code,
            "label": f"{p.name} ({code})",
            "episode_count": n,
        }
        for p, code, n in rows
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


    cfg = load_config()
    bucket_counts_raw = (
        db.query(ImportFinding.bucket, func.count(ImportFinding.id))
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


@router.get("/system", response_class=HTMLResponse)
def system_page(request: Request, db: Session = Depends(get_db)):
    """System info page: version, scheduler, stats, ABS config, config summary."""
    import importlib.metadata as _meta

    try:
        version = _meta.version("audiobiblio")
    except _meta.PackageNotFoundError:
        version = "dev"

    cfg = load_config()

    # Scheduler info from app.state (may be None in tests)
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_running = bool(scheduler.running) if scheduler is not None else False
    scheduler_jobs = (
        [{"id": j.id, "next_run_time": j.next_run_time} for j in scheduler.get_jobs()]
        if scheduler is not None
        else []
    )

    # Stats (same queries as /api/v1/stats)

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

    # ABS configuration: configured when abs_url is non-empty
    abs_configured = bool(cfg.abs_url)
    abs_url_display = cfg.abs_url if abs_configured else ""
    abs_key_redacted = "•••" if (abs_configured and cfg.abs_api_key) else ""

    return templates.TemplateResponse(request, "system.html", {
        "version": version,
        "scheduler_running": scheduler_running,
        "scheduler_jobs": scheduler_jobs,
        "ep_total": ep_total,
        "ep_avail": ep_avail,
        "ep_gone": ep_gone,
        "j_total": j_total,
        "j_pending": j_pending,
        "j_error": j_error,
        "j_success": j_success,
        "t_total": t_total,
        "t_active": t_active,
        "abs_configured": abs_configured,
        "abs_url_display": abs_url_display,
        "abs_key_redacted": abs_key_redacted,
        "library_dir": cfg.library_dir,
        "download_dir": cfg.download_dir,
        "inbox_dirs": cfg.inbox_dirs,
        "trash_retention_days": cfg.trash_retention_days,
        "active": "system",
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
    groups, _total, _total_jobs, _pages = _query_job_groups(db, status, page, limit=50)
    return templates.TemplateResponse(request, "_partials/job_rows.html", {
        "groups": groups,
    })
