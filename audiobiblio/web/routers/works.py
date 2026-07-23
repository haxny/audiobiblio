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

from audiobiblio.core.db.models import FieldOrigin, Work
from audiobiblio.core.provenance import record_value
from audiobiblio.core.db.session import get_engine
from audiobiblio.library.pipelines.completeness import complete_audio_count
from audiobiblio.library.pipelines.finalize import finalize_work
from audiobiblio.library.pipelines.library import default_library_root
from audiobiblio.sources.databazeknih import enrich_work_from_dbk
from ..deps import get_db
from ..tasks import task_tracker

router = APIRouter(prefix="/api/v1/works", tags=["works"])


def _resolved_final_path(db: Session, work_id: int) -> str | None:
    """Resolved `final_path` provenance value — set once a book sits on the
    curated shelf (auto_finalize or a user-offline adoption). Finalize must
    never move such a work again."""
    from audiobiblio.core.db.models import MetadataValue
    from audiobiblio.core.provenance import resolve_field

    rows = db.query(MetadataValue).filter_by(
        entity_type="work", entity_id=work_id, field="final_path").all()
    winner = resolve_field(rows)
    return winner.value if winner else None


class WorkExpectedTotalRequest(BaseModel):
    expected_total: int | None


class WorkExpectedTotalResponse(BaseModel):
    id: int
    expected_total: int | None
    expected_source: str | None


@router.patch("/{work_id}", response_model=WorkExpectedTotalResponse)
def patch_work(
    work_id: int,
    body: WorkExpectedTotalRequest,
    db: Session = Depends(get_db),
) -> WorkExpectedTotalResponse:
    """Set or clear the expected episode total for a work (manual provenance).

    Passing ``null`` clears both ``expected_total`` and ``expected_source`` and
    records a MANUAL provenance row with value=None (so the clear is auditable).

    422 when expected_total is a non-null integer <= 0.
    404 when the work does not exist.

    Records a MANUAL MetadataValue row (entity_type="work", field="expected_total")
    so that provenance history is preserved and the value survives sync cycles.
    """
    if body.expected_total is not None and body.expected_total <= 0:
        raise HTTPException(422, "expected_total must be a positive integer")

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(404, "Work not found")

    if body.expected_total is None:
        work.expected_total = None
        work.expected_source = None
        prov_value: str | None = None
    else:
        work.expected_total = body.expected_total
        work.expected_source = "manual"
        prov_value = str(body.expected_total)

    # Record MANUAL provenance (upsert: same key → update value + observed_at)
    record_value(
        db,
        entity_type="work",
        entity_id=work_id,
        field="expected_total",
        value=prov_value,
        origin=FieldOrigin.MANUAL,
        source="user",
    )

    db.commit()

    return WorkExpectedTotalResponse(
        id=work.id,
        expected_total=work.expected_total,
        expected_source=work.expected_source,
    )


class WorkMetadataEditRequest(BaseModel):
    field: str
    value: str


class WorkMetadataEditResponse(BaseModel):
    field: str
    value: str
    applied: bool
    episodes_updated: int


# Book-level metadata: title/author/year/publisher/translator live on the
# Work; narrator, genre and description are EPISODE-level (provenance) —
# editing them on the book fans the MANUAL value out to every part.
_WORK_META_FIELDS = {"title", "author", "year", "publisher", "translator", "www"}
_FANOUT_FIELDS = {"narrator", "genre", "description"}


@router.patch("/{work_id}/metadata", response_model=WorkMetadataEditResponse)
def edit_work_metadata(
    work_id: int,
    body: WorkMetadataEditRequest,
    db: Session = Depends(get_db),
) -> WorkMetadataEditResponse:
    """Record a MANUAL metadata value for a whole book.

    author/year/publisher → the Work (publisher is provenance-only);
    narrator/genre/description → MANUAL row on EVERY episode of the work
    (sync engine then projects them into file tags part by part).
    """
    allowed = _WORK_META_FIELDS | _FANOUT_FIELDS
    if body.field not in allowed:
        raise HTTPException(400, f"Unknown field '{body.field}'. Allowed: {sorted(allowed)}")
    if not body.value or not body.value.strip():
        raise HTTPException(422, "value must be non-empty")
    if body.field == "year":
        try:
            int(body.value)
        except ValueError:
            raise HTTPException(422, "year must be an integer value (e.g. '2025')")

    work = (
        db.query(Work)
        .options(joinedload(Work.episodes))
        .filter(Work.id == work_id)
        .first()
    )
    if work is None:
        raise HTTPException(404, "Work not found")

    # Binding user rule: NO Czech diacritics in tag-bound metadata — every
    # value stored here ends up in ID3 tags via sync, so strip at the door.
    from unidecode import unidecode
    value = unidecode(body.value.strip())
    applied = False
    episodes_updated = 0

    if body.field in _WORK_META_FIELDS:
        record_value(db, "work", work.id, body.field, value, FieldOrigin.MANUAL, "user")
        if body.field == "title":
            work.title = value
            applied = True
        elif body.field == "author":
            work.author = value
            applied = True
        elif body.field == "year":
            work.year = int(value)
            applied = True
    else:
        for ep in work.episodes:
            record_value(db, "episode", ep.id, body.field, value, FieldOrigin.MANUAL, "user")
            if body.field == "description":
                ep.summary = value
            episodes_updated += 1
        applied = body.field == "description"

    db.commit()
    return WorkMetadataEditResponse(
        field=body.field, value=value,
        applied=applied, episodes_updated=episodes_updated,
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


class FinalizeRequest(BaseModel):
    dry_run: bool = True


class FinalizeResponse(BaseModel):
    actions: list[str]
    applied: bool
    errors: list[str]


@router.post("/{work_id}/finalize", response_model=FinalizeResponse)
def finalize_endpoint(
    work_id: int,
    body: FinalizeRequest,
    db: Session = Depends(get_db),
) -> FinalizeResponse:
    """Move a complete Work's files into a per-work subfolder (explicit only).

    Guards:
        404 — work not found.
        409 — expected_total is unset (set it via PATCH /api/v1/works/{id}).
        409 — work is incomplete (have < expected_total).

    Default is dry_run=true: returns the planned actions without touching
    files.  The UI shows this preview first; a second call with
    dry_run=false applies the moves.

    Safety (enforced in library.pipelines.finalize):
        - moves only, never deletes; collisions get -2/-3 suffixes
        - existing directories are never renamed
        - session.flush() before every file move
    """
    work = (
        db.query(Work)
        .options(joinedload(Work.episodes))
        .filter(Work.id == work_id)
        .first()
    )
    if work is None:
        raise HTTPException(404, "Work not found")

    if work.expected_total is None:
        raise HTTPException(
            409,
            "expected_total is unset — set it via PATCH /api/v1/works/{id} first",
        )

    have = complete_audio_count(db, work.id)
    if have < work.expected_total:
        raise HTTPException(
            409,
            f"Work is incomplete: {have}/{work.expected_total} episodes have complete audio",
        )

    final_path = _resolved_final_path(db, work_id)
    if final_path:
        raise HTTPException(
            409,
            f"Work is already shelved at {final_path!r} — finalize would move it "
            "out of the curated library. Refusing.",
        )

    report = finalize_work(db, work, default_library_root(), dry_run=body.dry_run)
    if not body.dry_run:
        db.commit()

    return FinalizeResponse(
        actions=report.actions,
        applied=report.applied,
        errors=report.errors,
    )
