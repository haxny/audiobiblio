"""
routers/follow — follow files renamed/moved on disk.

POST /api/v1/library/follow   {"apply": false}  — scan the shelves, report (and apply) moves
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audiobiblio.library import pathfollow
from ..deps import get_db

router = APIRouter(prefix="/api/v1/library", tags=["follow"])


class FollowRequest(BaseModel):
    apply: bool = False
    backfill: int = 0


@router.post("/follow")
def follow(body: FollowRequest, db: Session = Depends(get_db)) -> dict:
    fingerprinted = pathfollow.backfill_fingerprints(db, limit=body.backfill) if body.backfill else 0
    rep = pathfollow.follow_paths(db, [Path(r) for r in pathfollow.DEFAULT_ROOTS], apply=body.apply)
    return {
        "applied": body.apply,
        "fingerprinted": fingerprinted,
        "moved": [{"asset_id": m.asset_id, "old": m.old, "new": m.new} for m in rep.moved],
        "ambiguous": [{"asset_id": a, "candidates": c} for a, c in rep.ambiguous],
        "unmatched": [{"asset_id": a, "path": p} for a, p in rep.unmatched],
        "works_updated": [{"work_id": w, "old": o, "new": n} for w, o, n in rep.works_updated],
    }
