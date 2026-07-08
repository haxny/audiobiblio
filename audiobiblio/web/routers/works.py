"""
routers/works — Work-level API endpoints.

Separated from routers/episodes.py because works are a distinct entity
(one Work has many Episodes) and mixing work-level and episode-level
operations in one router creates cognitive overhead.
"""
from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, sessionmaker

from audiobiblio.core.db.models import FieldOrigin, Work, Episode
from audiobiblio.core.provenance import record_value
from audiobiblio.core.db.session import get_engine
from audiobiblio.sources.databazeknih import enrich_work_from_dbk
from ..deps import get_db
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/works", tags=["works"])


class WorkExpectedTotalRequest(BaseModel):
    expected_total: int


class WorkExpectedTotalResponse(BaseModel):
    id: int
    expected_total: int
    expected_source: str


@router.patch("/{work_id}", response_model=WorkExpectedTotalResponse)
def patch_work(
    work_id: int,
    body: WorkExpectedTotalRequest,
    db: Session = Depends(get_db),
) -> WorkExpectedTotalResponse:
    """Set the expected episode total for a work (manual provenance).

    422 when expected_total <= 0.
    404 when the work does not exist.

    Records a MANUAL MetadataValue row (entity_type="work", field="expected_total")
    so that provenance history is preserved and the value survives sync cycles.
    """
    if body.expected_total <= 0:
        raise HTTPException(422, "expected_total must be a positive integer")

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(404, "Work not found")

    work.expected_total = body.expected_total
    work.expected_source = "manual"

    # Record MANUAL provenance (upsert: same key → update value + observed_at)
    record_value(
        db,
        entity_type="work",
        entity_id=work_id,
        field="expected_total",
        value=str(body.expected_total),
        origin=FieldOrigin.MANUAL,
        source="user",
    )

    db.commit()

    return WorkExpectedTotalResponse(
        id=work.id,
        expected_total=work.expected_total,
        expected_source=work.expected_source,
    )


class WorkEnrichResponse(BaseModel):
    task_id: str


@router.post("/{work_id}/enrich", response_model=WorkEnrichResponse)
def enrich_work(
    work_id: int,
    db: Session = Depends(get_db),
) -> WorkEnrichResponse:
    """Trigger databazeknih enrichment for a work in the background.

    Returns 404 when the work does not exist.
    Otherwise submits the enrichment to task_tracker and returns a task_id
    immediately (the enrichment runs in a background thread with its own
    DB session — own session pattern).

    This endpoint is fire-and-forget: the caller reloads the page after
    the 200 response (via apiJson). No data from the background task is
    returned to the caller.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(404, "Work not found")

    def _task() -> dict:
        engine = get_engine()
        _Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        session = _Session()
        try:
            w = (
                session.query(Work)
                .options(joinedload(Work.episodes))
                .filter(Work.id == work_id)
                .first()
            )
            if w is None:
                return {"error": "work not found"}
            report = enrich_work_from_dbk(session, w)
            return {
                "skipped": report.skipped,
                "reason": report.reason,
                "fields_set": report.fields_set,
            }
        finally:
            session.close()

    task_id = task_tracker.submit(f"enrich_work_{work_id}", _task)
    return WorkEnrichResponse(task_id=task_id)
