"""routers/live — read-only list of live recordings (planned, in progress,
recorded, failed) made by acquire/live."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import LiveRecording, LiveStatus
from audiobiblio.web.deps import get_db

router = APIRouter(prefix="/api/v1/live-recordings", tags=["live-recordings"])

MAX_LIMIT = 500


def serialize(r: LiveRecording) -> dict:
    return {
        "id": r.id, "title": r.title, "series_title": r.series_title, "kind": r.kind,
        "program_name": r.program_name,
        "station_code": r.station_code, "part": r.part, "total_parts": r.total_parts,
        "status": r.status.value if r.status else None,
        "window_start": r.window_start.isoformat() if r.window_start else None,
        "window_end": r.window_end.isoformat() if r.window_end else None,
        "schedule_start": r.schedule_start.isoformat() if r.schedule_start else None,
        "duration_s": r.duration_s, "coverage": r.coverage, "runs": len(r.runs or []),
        "file_path": r.file_path, "episode_id": r.episode_id, "error": r.error,
        "stream_url": r.stream_url,
    }


def status_counts(db: Session) -> dict[str, int]:
    return {s.value: n for s, n in db.query(LiveRecording.status, func.count())
            .group_by(LiveRecording.status).all()}


@router.get("")
def list_recordings(status: str | None = None, limit: int = Query(200, ge=1, le=MAX_LIMIT),
                    db: Session = Depends(get_db)):
    q = db.query(LiveRecording)
    if status:
        try:
            q = q.filter(LiveRecording.status == LiveStatus(status))
        except ValueError:
            raise HTTPException(422, f"unknown status {status!r}; one of "
                                     f"{', '.join(s.value for s in LiveStatus)}")
    rows = q.order_by(LiveRecording.window_start.desc()).limit(limit).all()
    return {"total": q.count(), "counts": status_counts(db), "items": [serialize(r) for r in rows]}
