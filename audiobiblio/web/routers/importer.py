"""
routers/importer — Import scan & review API.

Endpoints (prefix: /api/v1/import)
-----------------------------------
POST /scan                       — submit background directory scan
GET  /findings                   — list findings (bucket/status filter)
POST /findings/{id}/accept       — accept finding (link file to episode)
POST /findings/{id}/ignore       — ignore finding (skip file)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from audiobiblio.core.config import load_config
from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, ImportBucket, ImportFinding
from audiobiblio.library.importer import accept_finding, ignore_finding
from audiobiblio.library.trash import move_to_trash
from ..deps import get_db
from ..schemas import TaskResponse
from ..tasks import task_tracker

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/import", tags=["importer"])

_VALID_BUCKETS = {b.value for b in ImportBucket}


# ---------------------------------------------------------------------------
# Background task — mirrors _do_stage_upgrade pattern from upgrades.py
# ---------------------------------------------------------------------------

def _do_import_scan(root: Path) -> str:
    from audiobiblio.library.importer import scan_directory
    from audiobiblio.core.db.session import get_session
    import uuid as _uuid

    session = get_session()
    try:
        scan_id = _uuid.uuid4().hex
        report = scan_directory(session, root, scan_id=scan_id)
        return (
            f"scan_id={scan_id} total={report.total} "
            f"matched={report.matched} unknown={report.unknown}"
        )
    except Exception:
        log.error("import_scan.failed", root=str(root), exc_info=True)
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    root: Optional[str] = None
    inbox: bool = False


class AcceptBody(BaseModel):
    move: bool = False
    episode_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/scan", response_model=TaskResponse, status_code=202)
def scan(body: ScanRequest) -> TaskResponse:
    """Submit a background import scan.

    root=null → cfg.library_dir; inbox=true → one task per cfg.inbox_dirs entry.
    """
    cfg = load_config()

    if body.inbox:
        if not cfg.inbox_dirs:
            raise HTTPException(400, "No inbox_dirs configured")
        task_id: Optional[str] = None
        for dir_str in cfg.inbox_dirs:
            root_path = Path(dir_str).expanduser().resolve()
            task_id = task_tracker.submit("import_scan", _do_import_scan, root_path)
    else:
        if body.root is None:
            root_path = Path(cfg.library_dir).expanduser().resolve()
        else:
            root_path = Path(body.root).expanduser().resolve()
        task_id = task_tracker.submit("import_scan", _do_import_scan, root_path)

    return TaskResponse(task_id=task_id, name="import_scan", status="running")


@router.get("/findings")
def list_findings(
    bucket: Optional[str] = Query(None),
    status: str = Query("new"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """List import findings, optionally filtered by bucket and status."""
    if bucket is not None and bucket not in _VALID_BUCKETS:
        raise HTTPException(
            400,
            f"Invalid bucket: {bucket!r}. Valid values: {sorted(_VALID_BUCKETS)}",
        )

    q = db.query(ImportFinding).options(joinedload(ImportFinding.episode))
    q = q.filter(ImportFinding.status == status)
    if bucket is not None:
        q = q.filter(ImportFinding.bucket == ImportBucket(bucket))

    total = q.count()
    items = q.order_by(ImportFinding.id.desc()).offset(offset).limit(limit).all()

    return {
        "items": [
            {
                "id": f.id,
                "path": f.path,
                "bucket": f.bucket.value,
                "status": f.status,
                "episode_id": f.episode_id,
                "details": f.details,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
                "episode_title": f.episode.title if f.episode else None,
            }
            for f in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/findings/{finding_id}/accept")
def accept_finding_endpoint(
    finding_id: int,
    body: AcceptBody,
    db: Session = Depends(get_db),
) -> dict:
    """Accept an import finding — link the file to its episode as an audio asset."""
    finding = db.get(ImportFinding, finding_id)
    if finding is None:
        raise HTTPException(404, f"ImportFinding {finding_id} not found")
    if finding.status != "new":
        raise HTTPException(409, f"Finding already {finding.status!r}")

    if body.episode_id is not None:
        episode = db.get(Episode, body.episode_id)
        if episode is None:
            raise HTTPException(404, f"Episode {body.episode_id} not found")
        finding.episode_id = body.episode_id
        db.add(finding)
        db.flush()

    # Guard: episode_id must be resolved by now (either from finding or from body).
    if finding.episode_id is None:
        raise HTTPException(400, "episode_id is required: finding has no linked episode")

    # Guard: for non-DUPLICATE findings, reject if the episode already has a COMPLETE
    # audio asset at a *different* path — caller should re-scan to get a DUPLICATE bucket.
    if finding.bucket != ImportBucket.DUPLICATE:
        existing_complete = (
            db.query(Asset)
            .filter_by(
                episode_id=finding.episode_id,
                type=AssetType.AUDIO,
                status=AssetStatus.COMPLETE,
            )
            .first()
        )
        if (
            existing_complete
            and existing_complete.file_path
            and existing_complete.file_path != finding.path
        ):
            raise HTTPException(
                409,
                "episode already has a complete file; re-scan to classify as duplicate",
            )

    cfg = load_config()
    lib_dir = Path(cfg.library_dir).expanduser().resolve()

    log_msgs = accept_finding(
        db,
        finding,
        move=body.move,
        library_dir=lib_dir,
        trash_fn=move_to_trash,
    )
    return {"ok": True, "log": log_msgs}


@router.post("/findings/{finding_id}/ignore")
def ignore_finding_endpoint(
    finding_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Ignore an import finding — mark it so it won't be re-opened on re-scan."""
    finding = db.get(ImportFinding, finding_id)
    if finding is None:
        raise HTTPException(404, f"ImportFinding {finding_id} not found")
    if finding.status != "new":
        raise HTTPException(409, f"Finding already {finding.status!r}")

    ignore_finding(db, finding)
    return {"ok": True}
