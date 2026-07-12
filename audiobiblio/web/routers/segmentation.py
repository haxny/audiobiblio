"""segmentation API router — proposal and apply endpoints.

GET  /api/v1/segmentation/{program_id}
    → 404 if program not found
    → call propose_segmentation(db, program)
    → serialize SegmentationProposal to JSON

POST /api/v1/segmentation/{program_id}/apply
    body: {"dry_run": bool, "titles": list[str] | null}
    → 404 if program not found
    → propose then apply_segmentation
    → {"actions": list[str], "applied": bool}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audiobiblio.web.deps import get_db

router = APIRouter(prefix="/api/v1/segmentation", tags=["segmentation"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EpisodeSummaryJSON(BaseModel):
    id: int
    title: str
    episode_number: int | None


class ProposedWorkJSON(BaseModel):
    title: str
    author: str | None
    episode_count: int
    episode_ids: list[int]
    episodes: list[EpisodeSummaryJSON] = []
    signal: str
    confidence: float


class ProposalResponse(BaseModel):
    mode: str
    proposed: list[ProposedWorkJSON]
    unassigned_count: int
    note: str


class ApplyRequest(BaseModel):
    dry_run: bool = True
    titles: list[str] | None = None


class ApplyResponse(BaseModel):
    actions: list[str]
    applied: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{program_id}", response_model=ProposalResponse)
def proposal_endpoint(
    program_id: int,
    db: Session = Depends(get_db),
) -> ProposalResponse:
    """Return a segmentation proposal for the given program as JSON."""
    from audiobiblio.core.db.models import Program
    from audiobiblio.library.segmentation import propose_segmentation

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found")

    proposal = propose_segmentation(db, program)

    # Episode summaries let the user make an informed decision per proposal
    # (which parts, what numbers) instead of judging identical title rows.
    from audiobiblio.core.db.models import Episode
    all_ids = {i for pw in proposal.proposed for i in pw.episode_ids}
    eps = {
        e.id: e
        for e in db.query(Episode).filter(Episode.id.in_(all_ids)).all()
    } if all_ids else {}

    proposed = [
        ProposedWorkJSON(
            title=pw.title,
            author=pw.author,
            episode_count=len(pw.episode_ids),
            episode_ids=list(pw.episode_ids),
            episodes=[
                EpisodeSummaryJSON(
                    id=i,
                    title=eps[i].title if i in eps else "?",
                    episode_number=eps[i].episode_number if i in eps else None,
                )
                for i in pw.episode_ids
            ],
            signal=pw.signal,
            confidence=pw.confidence,
        )
        for pw in proposal.proposed
    ]
    # Stable, scannable order: multi-part books first, then by author/title.
    from unidecode import unidecode
    proposed.sort(key=lambda p: (
        -p.episode_count,
        unidecode(p.author or "~").lower(),
        unidecode(p.title).lower(),
    ))

    return ProposalResponse(
        mode=proposal.mode,
        proposed=proposed,
        unassigned_count=len(proposal.unassigned),
        note=proposal.note,
    )


@router.post("/{program_id}/apply", response_model=ApplyResponse)
def apply_endpoint(
    program_id: int,
    body: ApplyRequest,
    db: Session = Depends(get_db),
) -> ApplyResponse:
    """Apply (or dry-run) a segmentation proposal for the given program."""
    from audiobiblio.core.db.models import Program
    from audiobiblio.library.segmentation import apply_segmentation, propose_segmentation

    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found")

    proposal = propose_segmentation(db, program)
    only_titles = set(body.titles) if body.titles is not None else None
    actions = apply_segmentation(
        db,
        proposal,
        dry_run=body.dry_run,
        only_titles=only_titles,
    )

    return ApplyResponse(actions=actions, applied=not body.dry_run)
