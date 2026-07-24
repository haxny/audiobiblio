"""
routers/episodes — Episode listing, detail, audio streaming, and manual
metadata editing.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from audiobiblio.core.db.models import (
    Episode, Work, Series, Program, Asset, DownloadJob,
    AvailabilityStatus, AssetType, AssetStatus, FieldOrigin,
)
from audiobiblio.core.provenance import record_value, WORK_FIELDS as _WORK_ORM_FIELDS
from ..deps import get_db
from ..schemas import (
    EpisodeResponse, EpisodeDetailResponse, PaginatedEpisodes,
    AssetResponse, JobResponse, MetadataEditRequest, MetadataEditResponse,
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


_ALLOWED_FIELDS = {"title", "description", "author", "year", "narrator", "genre"}

# Fields that map directly to an ORM column; all others are provenance-only
_EPISODE_ORM_FIELDS = {"title", "description"}
# _WORK_ORM_FIELDS imported from core.provenance as WORK_FIELDS — single source of truth

# episode.field → (entity_type, field_name_in_provenance)
_EPISODE_ENTITY = "episode"
_WORK_ENTITY = "work"


@router.patch("/{episode_id}/metadata", response_model=MetadataEditResponse)
def edit_episode_metadata(
    episode_id: int,
    body: MetadataEditRequest,
    db: Session = Depends(get_db),
) -> MetadataEditResponse:
    """Record a MANUAL metadata value for an episode or its parent Work.

    Allowed fields: title, description (episode-level); author, year (work-level);
    narrator, genre (provenance-only — no ORM column; sync engine projects to tags).

    Returns applied=True when an ORM column was updated, False for provenance-only fields.
    Errors: 400 unknown field, 404 episode not found, 422 empty value or non-integer year.
    """
    if body.field not in _ALLOWED_FIELDS:
        raise HTTPException(400, f"Unknown field '{body.field}'. Allowed: {sorted(_ALLOWED_FIELDS)}")
    if not body.value or not body.value.strip():
        raise HTTPException(422, "value must be non-empty")
    if body.field == "year":
        try:
            int(body.value)
        except ValueError:
            raise HTTPException(422, "year must be an integer value (e.g. '2023')")

    ep = (
        db.query(Episode)
        .options(joinedload(Episode.work))
        .filter(Episode.id == episode_id)
        .first()
    )
    if ep is None:
        raise HTTPException(404, "Episode not found")

    work = ep.work

    # Determine provenance entity
    if body.field in _WORK_ORM_FIELDS:
        entity_type = _WORK_ENTITY
        entity_id = work.id
    else:
        entity_type = _EPISODE_ENTITY
        entity_id = ep.id

    # ALWAYS record MANUAL provenance (upsert: same key → update value + observed_at)
    record_value(db, entity_type, entity_id, body.field, body.value, FieldOrigin.MANUAL, "user")

    # Apply to ORM column where one exists
    applied = False
    if body.field == "title":
        ep.title = body.value
        applied = True
    elif body.field == "description":
        ep.summary = body.value
        applied = True
    elif body.field == "author":
        work.author = body.value
        applied = True
    elif body.field == "year":
        work.year = int(body.value)
        applied = True
    # narrator, genre: provenance-only; applied stays False

    db.commit()

    return MetadataEditResponse(
        field=body.field,
        value=body.value,
        origin="manual",
        applied=applied,
    )


# Media types for the preview player; anything else streams as a binary blob.
_AUDIO_MEDIA_TYPES = {
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mp3": "audio/mpeg",
}


@router.get("/{episode_id}/audio")
def episode_audio(episode_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream the episode's COMPLETE audio asset for the preview player.

    404 when the episode is unknown, has no COMPLETE audio asset, or the
    asset's file is gone from disk.  Starlette's FileResponse handles HTTP
    Range requests (206), so <audio> seeking works.
    """
    ep = (
        db.query(Episode)
        .options(joinedload(Episode.assets))
        .filter(Episode.id == episode_id)
        .first()
    )
    if ep is None:
        raise HTTPException(404, "Episode not found")

    asset = next(
        (
            a for a in ep.assets
            if a.type == AssetType.AUDIO and a.status == AssetStatus.COMPLETE
        ),
        None,
    )
    if asset is None or not asset.file_path:
        raise HTTPException(404, "No complete audio asset for this episode")

    path = Path(asset.file_path)
    if not path.is_file():
        raise HTTPException(404, "Audio file not found on disk")

    media_type = _AUDIO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


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


@router.get("/{episode_id}/peaks")
def episode_peaks(episode_id: int, db: Session = Depends(get_db)):
    """Waveform peaks for the player (podcast-style volume envelope).

    ~800 RMS buckets computed with ffmpeg on first request, cached as JSON
    in /tmp/peaks/. Spoken-word waveforms make pauses/music/chapters visible.
    """
    import json as _json
    import math
    import struct
    import subprocess
    from pathlib import Path as _Path

    from fastapi import Response as _Response

    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType

    asset = (
        db.query(Asset)
        .filter_by(episode_id=episode_id, type=AssetType.AUDIO,
                   status=AssetStatus.COMPLETE)
        .first()
    )
    if not asset or not asset.file_path or not _Path(asset.file_path).exists():
        raise HTTPException(404, "no audio file")

    cache_dir = _Path("/tmp/peaks")
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / f"{episode_id}.json"
    src_mtime = _Path(asset.file_path).stat().st_mtime
    if cache.exists() and cache.stat().st_mtime >= src_mtime:
        return _Response(cache.read_bytes(), media_type="application/json",
                         headers={"Cache-Control": "max-age=86400"})

    N_BUCKETS = 800
    proc = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", asset.file_path,
         "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
        capture_output=True, timeout=120,
    )
    raw = proc.stdout
    n_samples = len(raw) // 2
    if n_samples < N_BUCKETS:
        raise HTTPException(500, "audio decode failed")
    bucket = n_samples // N_BUCKETS
    peaks = []
    for i in range(N_BUCKETS):
        seg = raw[i * bucket * 2:(i + 1) * bucket * 2]
        vals = struct.unpack(f"<{len(seg) // 2}h", seg)
        rms = math.sqrt(sum(v * v for v in vals) / max(len(vals), 1))
        peaks.append(rms)
    top = max(peaks) or 1.0
    payload = _json.dumps({
        "peaks": [round(v / top, 3) for v in peaks],
        "duration": n_samples / 4000.0,
    }).encode()
    cache.write_bytes(payload)
    return _Response(payload, media_type="application/json",
                     headers={"Cache-Control": "max-age=86400"})
