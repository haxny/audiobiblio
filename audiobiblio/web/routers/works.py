"""
routers/works — Work-level API endpoints.

Separated from routers/episodes.py because works are a distinct entity
(one Work has many Episodes) and mixing work-level and episode-level
operations in one router creates cognitive overhead.
"""
from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import FieldOrigin, Work
from audiobiblio.core.provenance import record_value
from ..deps import get_db

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
