"""
routers/ingest — Program catalog, discovery, preview, and full ingest.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ...db.models import (
    Episode as EpModel, Program as ProgModel, Station,
    CrawlTarget, CrawlTargetKind, Series, Work,
)
from ...discovery import normalize_rozhlas_url
from ..deps import get_db
from ..schemas import (
    IngestProgramRequest, IngestPreviewResponse, IngestUrlRequest, TaskResponse,
    ProgramResponse, ProgramCatalogResponse, StationWithPrograms,
    AddProgramRequest, AddProgramResponse, UpdateProgramRequest,
)
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


# ── Program catalog endpoints ────────────────────────────────────────

def _program_to_response(prog: ProgModel, episode_count: int = 0) -> ProgramResponse:
    crawl_target = None
    if prog.url:
        from ...db.session import get_session
        s = get_session()
        crawl_target = s.query(CrawlTarget).filter_by(url=prog.url).first()
    return ProgramResponse(
        id=prog.id,
        name=prog.name,
        station_code=prog.station.code,
        station_name=prog.station.name,
        url=prog.url,
        genre=prog.genre,
        episode_count=episode_count,
        crawl_active=crawl_target.active if crawl_target else False,
        last_crawled=crawl_target.last_crawled_at if crawl_target else prog.last_crawled_at,
    )


@router.get("/programs", response_model=ProgramCatalogResponse)
def list_programs(db: Session = Depends(get_db)):
    """List all programs grouped by station."""
    programs = (
        db.query(ProgModel)
        .options(joinedload(ProgModel.station))
        .order_by(ProgModel.name)
        .all()
    )

    # Count episodes per program via series → works → episodes
    ep_counts: dict[int, int] = {}
    rows = (
        db.query(ProgModel.id, func.count(EpModel.id))
        .outerjoin(Series, Series.program_id == ProgModel.id)
        .outerjoin(Work, Work.series_id == Series.id)
        .outerjoin(EpModel, EpModel.work_id == Work.id)
        .group_by(ProgModel.id)
        .all()
    )
    for prog_id, count in rows:
        ep_counts[prog_id] = count

    # Look up crawl targets by URL for all programs at once
    prog_urls = [p.url for p in programs if p.url]
    crawl_targets: dict[str, CrawlTarget] = {}
    if prog_urls:
        targets = db.query(CrawlTarget).filter(CrawlTarget.url.in_(prog_urls)).all()
        crawl_targets = {t.url: t for t in targets}

    # Group by station
    by_station: dict[str, list[ProgramResponse]] = defaultdict(list)
    for prog in programs:
        ct = crawl_targets.get(prog.url) if prog.url else None
        resp = ProgramResponse(
            id=prog.id,
            name=prog.name,
            station_code=prog.station.code,
            station_name=prog.station.name,
            url=prog.url,
            genre=prog.genre,
            channel_label=prog.channel_label,
            episode_count=ep_counts.get(prog.id, 0),
            crawl_active=ct.active if ct else False,
            last_crawled=ct.last_crawled_at if ct else prog.last_crawled_at,
        )
        by_station[prog.station.code].append(resp)

    stations_list = [
        StationWithPrograms(
            code=code,
            name=progs[0].station_name,
            programs=progs,
        )
        for code, progs in sorted(by_station.items(), key=lambda x: x[1][0].station_name)
    ]

    return ProgramCatalogResponse(
        stations=stations_list,
        total_programs=len(programs),
    )


@router.post("/programs/add", response_model=AddProgramResponse)
def add_program(body: AddProgramRequest, db: Session = Depends(get_db)):
    """Add a program from URL. Creates Program + optional CrawlTarget."""
    url = normalize_rozhlas_url(body.url.strip())

    # Extract slug for naming
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[0] if path else ""
    if not slug:
        raise HTTPException(400, "Could not extract program slug from URL")

    # Determine station from slug
    from ...seed import _SLUG_STATION, _SLUG_DISPLAY, STATION_MAP
    station_code = _SLUG_STATION.get(slug, "mujrozhlas")
    station = db.query(Station).filter_by(code=station_code).first()
    if not station:
        # Fallback: create the station if needed
        st_info = STATION_MAP.get(station_code, (station_code, None))
        station = Station(code=station_code, name=st_info[0], website=st_info[1])
        db.add(station)
        db.flush()

    display_name = _SLUG_DISPLAY.get(slug, slug.replace("-", " ").title())
    norm_url = url.rstrip("/")

    # Check existing
    existing = db.query(ProgModel).filter_by(station_id=station.id, name=display_name).first()
    created = False
    if existing:
        prog = existing
        if not prog.url:
            prog.url = norm_url
            db.commit()
    else:
        prog = ProgModel(station_id=station.id, name=display_name, url=norm_url)
        db.add(prog)
        db.flush()
        created = True

    # Auto-create CrawlTarget if requested
    crawl_target_id = None
    if body.auto_crawl and norm_url:
        ct = db.query(CrawlTarget).filter_by(url=norm_url).first()
        if not ct:
            ct = CrawlTarget(
                url=norm_url,
                kind=CrawlTargetKind.PROGRAM,
                name=display_name,
                active=True,
            )
            db.add(ct)
            db.flush()
        crawl_target_id = ct.id

    db.commit()

    # Count episodes
    ep_count = (
        db.query(func.count(EpModel.id))
        .join(Work)
        .join(Series)
        .filter(Series.program_id == prog.id)
        .scalar() or 0
    )

    ct = db.query(CrawlTarget).filter_by(url=norm_url).first() if norm_url else None
    return AddProgramResponse(
        program=ProgramResponse(
            id=prog.id,
            name=prog.name,
            station_code=station.code,
            station_name=station.name,
            url=prog.url,
            genre=prog.genre,
            channel_label=prog.channel_label,
            episode_count=ep_count,
            crawl_active=ct.active if ct else False,
            last_crawled=ct.last_crawled_at if ct else prog.last_crawled_at,
        ),
        crawl_target_id=crawl_target_id,
        created=created,
    )


@router.patch("/programs/{program_id}", response_model=ProgramResponse)
def update_program(program_id: int, body: UpdateProgramRequest, db: Session = Depends(get_db)):
    """Edit a program's name, genre, channel_label, or URL."""
    prog = db.get(ProgModel, program_id)
    if not prog:
        raise HTTPException(404, "Program not found")

    if body.name is not None:
        prog.name = body.name
    if body.genre is not None:
        prog.genre = body.genre
    if body.channel_label is not None:
        prog.channel_label = body.channel_label
    if body.url is not None:
        prog.url = body.url

    db.commit()
    db.refresh(prog)

    # Episode count
    ep_count = (
        db.query(func.count(EpModel.id))
        .join(Work)
        .join(Series)
        .filter(Series.program_id == prog.id)
        .scalar() or 0
    )
    ct = db.query(CrawlTarget).filter_by(url=prog.url).first() if prog.url else None
    return ProgramResponse(
        id=prog.id,
        name=prog.name,
        station_code=prog.station.code,
        station_name=prog.station.name,
        url=prog.url,
        genre=prog.genre,
        channel_label=prog.channel_label,
        episode_count=ep_count,
        crawl_active=ct.active if ct else False,
        last_crawled=ct.last_crawled_at if ct else prog.last_crawled_at,
    )


def _do_preview(url: str, skip_ajax: bool, db: Session) -> dict:
    from ...discovery import discover_program
    from ...dedupe import dedupe_discovered

    url = normalize_rozhlas_url(url)
    discovered = discover_program(url, skip_ajax=skip_ajax)
    if not discovered:
        return {"raw_count": 0, "unique_count": 0, "reairs": 0, "already_in_db": 0, "episodes": []}

    existing_eps = db.query(EpModel).all()
    unique, dup_groups = dedupe_discovered(discovered, existing_episodes=existing_eps)

    already_in_db = sum(1 for g in dup_groups if g.canonical_url == "(existing in DB)")
    reairs = len(dup_groups) - already_in_db

    episodes = [
        {
            "title": ep.title,
            "url": ep.url,
            "series": ep.series,
            "description": ep.description,
            "published_at": ep.published_at,
            "duration_s": ep.duration_s,
            "sources": list(ep.sources) if hasattr(ep, "sources") else [],
            "is_series_episode": getattr(ep, "is_series_episode", False),
        }
        for ep in unique
    ]

    return {
        "raw_count": len(discovered),
        "unique_count": len(unique),
        "reairs": reairs,
        "already_in_db": already_in_db,
        "episodes": episodes,
    }


def _parse_published_at(val: str | None) -> datetime | None:
    """Parse a YYYY-MM-DD or YYYYMMDD string to datetime."""
    if not val:
        return None
    try:
        if len(val) == 8 and val.isdigit():
            return datetime.strptime(val, "%Y%m%d")
        return datetime.fromisoformat(val[:10])
    except (ValueError, TypeError):
        return None


def _do_ingest(url: str, genre: str, skip_ajax: bool, channel_label: str) -> str:
    from ...discovery import discover_program
    from ...dedupe import dedupe_discovered
    from ...pipelines.ingest import upsert_from_item, queue_assets_for_episode
    from ...db.session import get_session

    url = normalize_rozhlas_url(url)
    s = get_session()
    discovered = discover_program(url, skip_ajax=skip_ajax)
    if not discovered:
        return "No episodes discovered"

    existing_eps = s.query(EpModel).all()
    unique, dup_groups = dedupe_discovered(discovered, existing_episodes=existing_eps)

    if not unique:
        return "All episodes already in DB"

    first = unique[0]
    prog_uploader = first.uploader or ""
    prog_series = first.series or ""

    if genre or channel_label:
        from ...pipelines.ingest import _guess_station_from_uploader, _get_or_create_station
        code, st_name, st_url = _guess_station_from_uploader(prog_uploader)
        st = _get_or_create_station(s, code=code, name=st_name, website=st_url)
        prog_name = prog_series or prog_uploader or "mujrozhlas"
        prog = s.query(ProgModel).filter_by(station_id=st.id, name=prog_name).first()
        if prog:
            if genre:
                prog.genre = genre
            if channel_label:
                prog.channel_label = channel_label
            s.commit()

    dated = [(ep, ep.published_at or "") for ep in unique]
    dated.sort(key=lambda x: x[1], reverse=True)

    total_jobs = 0
    for priority, (ep, _) in enumerate(dated, 1):
        pub_dt = _parse_published_at(ep.published_at)
        dur_ms = ep.duration_s * 1000 if ep.duration_s else None
        db_ep, _work = upsert_from_item(
            s,
            url=ep.url,
            item_title=ep.title,
            series_name=ep.series or prog_series,
            author=ep.author,
            uploader=ep.uploader or prog_uploader,
            work_title=ep.series or prog_series or ep.title,
            episode_number=None,
            ext_id=ep.ext_id,
            discovery_source="web_ingest",
            priority=len(dated) - priority + 1,
            summary=ep.description,
            published_at=pub_dt,
            duration_ms=dur_ms,
        )
        jobs = queue_assets_for_episode(s, db_ep.id)
        total_jobs += len(jobs)

    return f"Ingested {len(unique)} episodes, queued {total_jobs} jobs"


@router.post("/program/preview", response_model=IngestPreviewResponse)
def ingest_preview(body: IngestProgramRequest, db: Session = Depends(get_db)):
    return _do_preview(body.url, body.skip_ajax, db)


@router.post("/program", response_model=TaskResponse)
def ingest_program(body: IngestProgramRequest):
    task_id = task_tracker.submit(
        "ingest",
        _do_ingest,
        body.url,
        body.genre,
        body.skip_ajax,
        body.channel_label,
    )
    return TaskResponse(task_id=task_id, name="ingest", status="running")


@router.post("/url", response_model=TaskResponse)
def ingest_url(body: IngestUrlRequest):
    def _do():
        from ...db.session import get_session
        from ...mrz_inspector import probe_url, classify_probe
        from ...pipelines.ingest import upsert_from_item, queue_assets_for_episode

        s = get_session()
        data = probe_url(body.url)
        pr = classify_probe(data, body.url)

        if pr.kind != "episode" or not pr.entries:
            return "Not a single episode URL"

        item = pr.entries[0]
        ep, _work = upsert_from_item(
            s,
            url=item.url,
            item_title=item.title,
            series_name=item.series or pr.series or pr.title,
            author=item.author,
            uploader=item.uploader or pr.uploader,
            work_title=pr.title if pr.series else item.series or item.title,
            episode_number=item.episode_number or 1,
        )
        jobs = queue_assets_for_episode(s, ep.id)
        return f"Queued episode {ep.id} with {len(jobs)} job(s)"

    task_id = task_tracker.submit("ingest_url", _do)
    return TaskResponse(task_id=task_id, name="ingest_url", status="running")
