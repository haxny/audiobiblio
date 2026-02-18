"""
routers/ingest — Program discovery, preview, and full ingest.
"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db.models import Episode as EpModel, Program as ProgModel
from ...discovery import normalize_rozhlas_url
from ..deps import get_db
from ..schemas import (
    IngestProgramRequest, IngestPreviewResponse, IngestUrlRequest, TaskResponse,
)
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


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
