"""
routers/targets — Crawl target CRUD and manual crawl trigger.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.models import CrawlTarget, CrawlTargetKind
from ..deps import get_db
from ..schemas import TargetResponse, TargetCreateRequest, TargetUpdateRequest, TaskResponse
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/targets", tags=["targets"])


def _target_to_response(t: CrawlTarget) -> TargetResponse:
    return TargetResponse(
        id=t.id,
        url=t.url,
        kind=t.kind.value,
        name=t.name,
        active=t.active,
        interval_hours=t.interval_hours,
        last_crawled_at=t.last_crawled_at,
        next_crawl_at=t.next_crawl_at,
        created_at=t.created_at,
    )


@router.get("", response_model=list[TargetResponse])
def list_targets(db: Session = Depends(get_db)):
    targets = db.query(CrawlTarget).order_by(CrawlTarget.id).all()
    return [_target_to_response(t) for t in targets]


@router.post("", response_model=TargetResponse, status_code=201)
def create_target(body: TargetCreateRequest, db: Session = Depends(get_db)):
    try:
        kind = CrawlTargetKind(body.kind.lower())
    except ValueError:
        raise HTTPException(400, f"Invalid kind: {body.kind}")

    existing = db.query(CrawlTarget).filter_by(url=body.url).first()
    if existing:
        raise HTTPException(409, f"Target already exists with id={existing.id}")

    t = CrawlTarget(
        url=body.url,
        kind=kind,
        name=body.name,
        interval_hours=body.interval_hours,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _target_to_response(t)


@router.patch("/{target_id}", response_model=TargetResponse)
def update_target(target_id: int, body: TargetUpdateRequest, db: Session = Depends(get_db)):
    t = db.get(CrawlTarget, target_id)
    if not t:
        raise HTTPException(404, "Target not found")

    if body.active is not None:
        t.active = body.active
    if body.interval_hours is not None:
        t.interval_hours = body.interval_hours
    if body.name is not None:
        t.name = body.name

    db.commit()
    db.refresh(t)
    return _target_to_response(t)


@router.delete("/{target_id}", status_code=204)
def delete_target(target_id: int, db: Session = Depends(get_db)):
    t = db.get(CrawlTarget, target_id)
    if not t:
        raise HTTPException(404, "Target not found")
    db.delete(t)
    db.commit()


@router.post("/{target_id}/crawl", response_model=TaskResponse)
def crawl_now(target_id: int, db: Session = Depends(get_db)):
    t = db.get(CrawlTarget, target_id)
    if not t:
        raise HTTPException(404, "Target not found")

    from ...crawler import crawl_target
    task_id = task_tracker.submit("crawl", crawl_target, t)
    return TaskResponse(task_id=task_id, name="crawl", status="running")
