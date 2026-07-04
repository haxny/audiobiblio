"""dedupe API router — POST /api/v1/dedupe/merge.

This router lives in the ``web`` layer, which may import from both
``library`` and ``dedupe``.  It wires move_to_trash into merge_episodes
as the trash_fn dependency, keeping the dedupe layer layer-clean.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audiobiblio.core.config import load_config
from audiobiblio.web.deps import get_db

router = APIRouter(prefix="/api/v1/dedupe", tags=["dedupe"])


class MergeRequest(BaseModel):
    canonical_id: int
    duplicate_id: int
    dry_run: bool = True


class MergeResponse(BaseModel):
    actions: list[str]
    dry_run: bool


@router.post("/merge", response_model=MergeResponse)
def merge_endpoint(
    body: MergeRequest, db: Session = Depends(get_db)
) -> MergeResponse:
    """Merge duplicate episode into canonical.

    Returns the list of actions taken (or that would be taken in dry_run mode).

    HTTP 409 if the duplicate carries MANUAL MetadataValue rows.
    HTTP 404 if either episode is not found.
    """
    from audiobiblio.dedupe.clusters import (
        ManualMetadataProtectionError,
        merge_episodes,
    )
    from audiobiblio.library.trash import move_to_trash

    cfg = load_config()
    library_dir = Path(cfg.library_dir).expanduser()

    def trash_fn(p: Path) -> Path:
        return move_to_trash(p, library_dir, reason="dedupe_merge")

    try:
        actions = merge_episodes(
            db,
            body.canonical_id,
            body.duplicate_id,
            library_dir,
            dry_run=body.dry_run,
            trash_fn=trash_fn if not body.dry_run else None,
        )
        return MergeResponse(actions=actions, dry_run=body.dry_run)
    except ManualMetadataProtectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
