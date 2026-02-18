"""
routers/episodes — Episode listing with search and availability filter.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ...db.models import (
    Episode, Work, Series, Program, Asset, DownloadJob,
    AvailabilityStatus, AssetType, AssetStatus,
)
from ..deps import get_db
from ..schemas import (
    EpisodeResponse, EpisodeDetailResponse, PaginatedEpisodes,
    AssetResponse, JobResponse,
)

router = APIRouter(prefix="/api/v1/episodes", tags=["episodes"])


def _audio_status(episode: Episode) -> str | None:
    for a in episode.assets:
        if a.type == AssetType.AUDIO:
            return a.status.value
    return None


def _episode_to_response(ep: Episode) -> EpisodeResponse:
    work = ep.work
    series = work.series if work else None
    program = series.program if series else None
    return EpisodeResponse(
        id=ep.id,
        title=ep.title,
        work_title=work.title if work else "",
        series_name=series.name if series else "",
        program_name=program.name if program else "",
        url=ep.url,
        episode_number=ep.episode_number,
        availability_status=ep.availability_status.value if ep.availability_status else None,
        audio_status=_audio_status(ep),
        created_at=ep.created_at,
    )


@router.get("", response_model=PaginatedEpisodes)
def list_episodes(
    q: str | None = Query(None),
    availability: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Episode).options(
        joinedload(Episode.work).joinedload(Work.series).joinedload(Series.program),
        joinedload(Episode.assets),
    )

    if q:
        pattern = f"%{q}%"
        query = query.filter(
            or_(
                Episode.title.ilike(pattern),
                Episode.url.ilike(pattern),
            )
        )

    if availability:
        try:
            avail = AvailabilityStatus(availability)
            query = query.filter(Episode.availability_status == avail)
        except ValueError:
            raise HTTPException(400, f"Invalid availability: {availability}")

    total = query.count()
    items = query.order_by(Episode.id.desc()).offset(offset).limit(limit).all()

    return PaginatedEpisodes(
        items=[_episode_to_response(ep) for ep in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{episode_id}", response_model=EpisodeDetailResponse)
def get_episode(episode_id: int, db: Session = Depends(get_db)):
    ep = db.query(Episode).options(
        joinedload(Episode.work).joinedload(Work.series).joinedload(Series.program),
        joinedload(Episode.assets),
        joinedload(Episode.jobs),
    ).get(episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")

    work = ep.work
    series = work.series if work else None
    program = series.program if series else None

    return EpisodeDetailResponse(
        id=ep.id,
        title=ep.title,
        work_title=work.title if work else "",
        series_name=series.name if series else "",
        program_name=program.name if program else "",
        url=ep.url,
        episode_number=ep.episode_number,
        availability_status=ep.availability_status.value if ep.availability_status else None,
        audio_status=_audio_status(ep),
        created_at=ep.created_at,
        summary=ep.summary,
        duration_ms=ep.duration_ms,
        published_at=ep.published_at,
        assets=[
            AssetResponse(
                id=a.id,
                type=a.type.value,
                status=a.status.value,
                file_path=a.file_path,
                source_url=a.source_url,
            )
            for a in ep.assets
        ],
        jobs=[
            JobResponse(
                id=j.id,
                episode_id=j.episode_id,
                episode_title=ep.title,
                work_title=work.title if work else "",
                asset_type=j.asset_type.value,
                status=j.status.value,
                error=j.error,
                created_at=j.created_at,
                started_at=j.started_at,
                finished_at=j.finished_at,
            )
            for j in ep.jobs
        ],
    )
