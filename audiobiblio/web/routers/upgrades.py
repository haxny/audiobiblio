"""
routers/upgrades — Upgrade lifecycle API.

Endpoints
---------
GET  /api/v1/upgrades               list candidates (filter by ?status=)
POST /api/v1/upgrades/{id}/stage    submit background download to staging dir
POST /api/v1/upgrades/{id}/resolve  resolve with replace | keep_old | dismiss

Resolve/replace crash-safety
-----------------------------
Steps are executed in a strict order; on failure the process logs and raises
(no automatic rollback):

  1. carry_over_tags(old → staged)
  2. move_to_trash(old)           ← old file safe in trash if crash here
  3. shutil.move(staged → old path)
  4. apply_media_info(asset, old path)
  5. status=REPLACED + resolved_at + commit

If the server crashes between steps 2 and 3 the old file sits in the dated
trash folder and the staged file remains in the staging dir.  Both are
recoverable.  The user must re-run resolve manually after restoring the
staged file to the expected staged_path.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from audiobiblio.core.config import load_config
from audiobiblio.core.db.models import Episode, UpgradeCandidate, UpgradeStatus
from audiobiblio.library.mediainfo import apply_media_info, read_media_info
from audiobiblio.library.trash import move_to_trash
from audiobiblio.tags.carryover import carry_over_tags
from ..deps import get_db
from ..schemas import (
    PaginatedUpgrades,
    ResolveRequest,
    ResolveResponse,
    TaskResponse,
    UpgradeCandidateResponse,
)
from ..tasks import task_tracker

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/upgrades", tags=["upgrades"])

_VALID_STATUSES = {s.value for s in UpgradeStatus}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate_to_response(c: UpgradeCandidate) -> UpgradeCandidateResponse:
    return UpgradeCandidateResponse(
        id=c.id,
        episode_id=c.episode_id,
        episode_title=c.episode.title if c.episode else "",
        candidate_url=c.candidate_url,
        candidate_duration_ms=c.candidate_duration_ms,
        owned_duration_ms=c.owned_duration_ms,
        owned_asset_id=c.owned_asset_id,
        status=c.status.value,
        staged_path=c.staged_path,
        note=c.note,
        created_at=c.created_at,
        resolved_at=c.resolved_at,
    )


def _do_stage_upgrade(candidate_id: int, staging_dir: Path) -> str:
    """Background task: download candidate URL to staging dir, update DB.

    Opens its own DB session (runs in a background thread).
    """
    from audiobiblio.acquire.downloader import download_to_staging
    from audiobiblio.core.db.session import get_session

    session = get_session()
    try:
        candidate = session.get(UpgradeCandidate, candidate_id)
        if not candidate:
            raise RuntimeError(f"UpgradeCandidate {candidate_id} not found")

        staged_path = download_to_staging(candidate.candidate_url, staging_dir)

        info = read_media_info(staged_path)
        note_parts = []
        if info.bitrate is not None:
            note_parts.append(f"bitrate={info.bitrate}")
        if info.duration_ms is not None:
            note_parts.append(f"duration_ms={info.duration_ms}")

        candidate.status = UpgradeStatus.STAGED
        candidate.staged_path = str(staged_path)
        candidate.note = "; ".join(note_parts) if note_parts else None
        session.commit()
        log.info("stage_upgrade.done", candidate_id=candidate_id, path=str(staged_path))
        return str(staged_path)
    except Exception:
        log.error("stage_upgrade.failed", candidate_id=candidate_id, exc_info=True)
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedUpgrades)
def list_upgrades(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(UpgradeCandidate).options(joinedload(UpgradeCandidate.episode))

    if status is not None:
        if status not in _VALID_STATUSES:
            raise HTTPException(400, f"Invalid status: {status!r}. "
                                f"Valid values: {sorted(_VALID_STATUSES)}")
        try:
            q = q.filter(UpgradeCandidate.status == UpgradeStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status!r}")

    total = q.count()
    items = q.order_by(UpgradeCandidate.id.desc()).offset(offset).limit(limit).all()

    return PaginatedUpgrades(
        items=[_candidate_to_response(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{candidate_id}/stage", response_model=TaskResponse, status_code=202)
def stage_upgrade(candidate_id: int, db: Session = Depends(get_db)):
    candidate = db.get(UpgradeCandidate, candidate_id)
    if not candidate:
        raise HTTPException(404, f"UpgradeCandidate {candidate_id} not found")

    if candidate.status != UpgradeStatus.PENDING_REVIEW:
        raise HTTPException(
            409,
            f"Cannot stage: candidate is already {candidate.status.value}. "
            "Only PENDING_REVIEW candidates can be staged.",
        )

    cfg = load_config()
    staging_dir = Path(cfg.download_dir) / "_staging" / f"upgrade-{candidate_id}"

    task_id = task_tracker.submit("stage_upgrade", _do_stage_upgrade, candidate_id, staging_dir)
    return TaskResponse(task_id=task_id, name="stage_upgrade", status="running")


@router.post("/{candidate_id}/resolve", response_model=ResolveResponse)
def resolve_upgrade(
    candidate_id: int,
    body: ResolveRequest,
    db: Session = Depends(get_db),
):
    valid_decisions = {"replace", "keep_old", "dismiss"}
    if body.decision not in valid_decisions:
        raise HTTPException(400, f"Invalid decision: {body.decision!r}. "
                            f"Valid values: {sorted(valid_decisions)}")

    candidate = db.get(
        UpgradeCandidate, candidate_id,
        options=[joinedload(UpgradeCandidate.owned_asset)]
    )
    if not candidate:
        raise HTTPException(404, f"UpgradeCandidate {candidate_id} not found")

    # Already resolved?
    terminal = {UpgradeStatus.REPLACED, UpgradeStatus.KEPT_OLD, UpgradeStatus.DISMISSED}
    if candidate.status in terminal:
        raise HTTPException(409, f"Candidate already resolved: {candidate.status.value}")

    cfg = load_config()
    library_dir = Path(cfg.library_dir).expanduser().resolve()

    now = datetime.utcnow()

    if body.decision == "replace":
        _resolve_replace(db, candidate, library_dir, now)

    elif body.decision == "keep_old":
        _resolve_keep_old(db, candidate, library_dir, now)

    else:  # dismiss
        _resolve_dismiss(db, candidate, library_dir, now)

    return ResolveResponse(
        id=candidate.id,
        status=candidate.status.value,
        resolved_at=candidate.resolved_at,
    )


# ---------------------------------------------------------------------------
# Resolution helpers (sequenced, crash-safe per spec)
# ---------------------------------------------------------------------------

def _resolve_replace(db: Session, candidate: UpgradeCandidate, library_dir: Path, now: datetime) -> None:
    """Execute the replace sequence.

    Requires status == STAGED.  Raises 409 otherwise.

    Crash-safety: if the server dies between steps, the old file is in the
    dated trash and the staged file remains in staging.  No automatic rollback
    — both files are recoverable.
    """
    if candidate.status != UpgradeStatus.STAGED:
        raise HTTPException(
            409,
            f"replace requires STAGED status, current: {candidate.status.value}",
        )

    owned_asset = candidate.owned_asset
    if not owned_asset or not owned_asset.file_path:
        raise HTTPException(409, "Owned asset has no file_path — cannot replace")

    if not candidate.staged_path:
        raise HTTPException(409, "No staged_path set on candidate")

    old_path = Path(owned_asset.file_path)
    staged_path = Path(candidate.staged_path)

    if not old_path.exists():
        raise HTTPException(409, f"Old file not found on disk: {old_path}")
    if not staged_path.exists():
        raise HTTPException(409, f"Staged file not found on disk: {staged_path}")

    # Step 1: carry curated tags from old → staged
    try:
        carry_over_tags(old_path, staged_path)
    except Exception as exc:
        log.warning("replace.carry_over_tags_failed", err=str(exc), candidate_id=candidate.id)
        # Non-fatal: continue — new file keeps its own tags

    # Step 2: move old file to trash (crash here → old in trash, staged intact)
    move_to_trash(old_path, library_dir, reason=f"upgrade:replaced by {staged_path.name}")

    # Step 3: move staged file to old file's exact library path
    shutil.move(str(staged_path), str(old_path))

    # Step 4: re-read quality metadata from the new file at old path
    try:
        owned_asset.size_bytes = old_path.stat().st_size
        apply_media_info(db, owned_asset, old_path)
    except Exception as exc:
        log.warning("replace.apply_media_info_failed", err=str(exc), candidate_id=candidate.id)
        db.commit()

    # Step 5: mark resolved
    candidate.status = UpgradeStatus.REPLACED
    candidate.resolved_at = now
    db.commit()


def _resolve_keep_old(db: Session, candidate: UpgradeCandidate, library_dir: Path, now: datetime) -> None:
    """Trash the staged file (if any) and mark as KEPT_OLD.

    Allowed from any non-terminal status (no staged file is fine).
    """
    if candidate.staged_path:
        staged_path = Path(candidate.staged_path)
        if staged_path.exists():
            move_to_trash(staged_path, library_dir, reason="upgrade:keep_old")

    candidate.status = UpgradeStatus.KEPT_OLD
    candidate.resolved_at = now
    db.commit()


def _resolve_dismiss(db: Session, candidate: UpgradeCandidate, library_dir: Path, now: datetime) -> None:
    """Trash the staged file (if any) and mark as DISMISSED.

    Allowed from any non-terminal status (including PENDING_REVIEW without
    a staged file — the candidate was reviewed and dismissed before staging).
    """
    if candidate.staged_path:
        staged_path = Path(candidate.staged_path)
        if staged_path.exists():
            move_to_trash(staged_path, library_dir, reason="upgrade:dismissed")

    candidate.status = UpgradeStatus.DISMISSED
    candidate.resolved_at = now
    db.commit()
