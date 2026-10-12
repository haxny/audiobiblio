"""routers/dup_review — temp copies of shelved books: proposals, the user's
decisions, and the executor that acts on them (library/dup_review)."""
from __future__ import annotations

import csv
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import DupCandidate
from audiobiblio.library import dup_review as dr
from audiobiblio.web.deps import get_db

router = APIRouter(prefix="/api/v1/dup-review", tags=["dup-review"])

REPORT_TSV = Path("/media/ebooks/dups_copy_of_shelved.tsv")


class DecideRequest(BaseModel):
    ids: list[int]
    decision: str | None = None


@router.post("/decide")
def decide(body: DecideRequest, db: Session = Depends(get_db)):
    try:
        updated = dr.decide(db, body.ids, body.decision)
    except ValueError as e:
        raise HTTPException(422, str(e))
    pending = db.query(DupCandidate).filter_by(status="decided").count()
    return {"updated": updated, "pending": pending}


@router.post("/accept-proposals")
def accept_proposals(db: Session = Depends(get_db)):
    return {"accepted": dr.accept_proposals(db)}


@router.post("/execute")
def execute(db: Session = Depends(get_db)):
    return dr.execute(db)


@router.post("/import")
def import_report(db: Session = Depends(get_db)):
    if not REPORT_TSV.exists():
        raise HTTPException(404, f"{REPORT_TSV} neexistuje")
    with REPORT_TSV.open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    return {"added": dr.import_rows(db, rows), "rows": len(rows)}


@router.get("/{cid}/listing")
def listing(cid: int, db: Session = Depends(get_db)):
    """Both folders' files side by side (review aid)."""
    c = db.get(DupCandidate, cid)
    if c is None:
        raise HTTPException(404, "kandidát neexistuje")

    def files(p: str) -> list[dict]:
        d = Path(p)
        if not d.is_dir():
            return []
        return [{"name": str(f.relative_to(d)), "mb": round(f.stat().st_size / 1e6, 1)}
                for f in sorted(d.rglob("*")) if f.is_file() and "@eaDir" not in str(f)]
    return {"temp": files(c.temp_path), "shelf": files(c.shelf_path)}
