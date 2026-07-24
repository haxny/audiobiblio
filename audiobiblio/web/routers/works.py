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
_WORK_META_FIELDS = {"title", "author", "year", "date", "subtitle", "publisher", "translator", "www", "series_number"}
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
    if body.field == "series_number":
        try:
            int(body.value)
        except ValueError:
            raise HTTPException(422, "series_number must be an integer (poradi v cyklu)")
    if body.field == "date":
        import re as _re
        if not _re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", body.value.strip()):
            raise HTTPException(
                422, "date must be YYYY, YYYY-MM or YYYY-MM-DD (TDRC/©day accept all)")

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
        elif body.field == "date":
            # full-or-partial broadcast/edition date; year column AND the
            # "year" provenance field mirror it (episode-level resolution
            # reads "year" — user: book date propagates to episodes)
            work.year = int(value[:4])
            record_value(db, "work", work.id, "year", value[:4],
                         FieldOrigin.MANUAL, "user")
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

    # Aim at the curated shelf (same destination logic as the librarian);
    # only unmapped programs fall back to the working-library layout.
    from audiobiblio.library.pipelines.auto_finalize import curated_destination
    dest, why_not = curated_destination(db, work)
    notes: list[str] = []
    if dest is None:
        notes.append(f"POZOR: kuratorsky cil nedostupny ({why_not}) — pracovni layout")

    report = finalize_work(db, work, default_library_root(), dry_run=body.dry_run,
                           dest_dir_override=dest)
    if not body.dry_run:
        db.commit()
        if dest is not None and report.moved and not report.errors:
            record_value(db, "work", work.id, "final_path", str(dest),
                         FieldOrigin.MANUAL, "finalize_button")
            db.commit()

    # Display translation: container mounts -> the user's share names.
    # /media/fiction IS /volume3/eBOOKs/eBOOKs.fiction (verified mount) —
    # the preview must say so instead of leaking container paths.
    _share = {"/media/fiction": "eBOOKs.fiction",
              "/media/nonfiction": "eBOOKs.nonfiction",
              "/media/audiobooks": "eBOOKs/audiobooks [pracovni]"}
    def _tr(text: str) -> str:
        for pref, share in _share.items():
            text = text.replace(pref, share)
        return text

    return FinalizeResponse(
        actions=[_tr(a) for a in (notes + report.actions)],
        applied=report.applied,
        errors=[_tr(e) for e in report.errors],
    )


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------

def _store_cover_candidate(db: Session, work_id: int, data: bytes, label: str) -> str | None:
    """Persist the source image into the shelved book's _meta/covers/ so the
    originals stay reviewable (user rule: source covers belong to _meta).
    Works only once the book has a final_path (its own directory)."""
    from pathlib import Path as _P
    from audiobiblio.library.cover import sniff_mime, work_audio_paths
    final = _resolved_final_path(db, work_id)
    if final:
        base = _P(final)
    else:
        paths = work_audio_paths(db, work_id)
        if not paths:
            return None
        base = _P(paths[0]).parent
        label = f"work{work_id}-{label}"
    covers = base / "_meta" / "covers"
    try:
        covers.mkdir(parents=True, exist_ok=True)
        ext = ".png" if sniff_mime(data) == "image/png" else ".jpg"
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)[:60]
        out = covers / f"{safe}{ext}"
        n = 2
        while out.exists() and out.read_bytes() != data:
            out = covers / f"{safe}-{n}{ext}"; n += 1
        out.write_bytes(data)
        return str(out)
    except Exception:
        return None


@router.get("/{work_id}/cover")
def get_cover(work_id: int, db: Session = Depends(get_db)):
    """Embedded cover of the work's first audio file (ABS-style: files are
    the source of truth for artwork)."""
    from fastapi import Response
    from audiobiblio.library.cover import get_work_cover
    found = get_work_cover(db, work_id)
    if not found:
        raise HTTPException(404, "no embedded cover")
    data, mime = found
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "no-cache"})


class CoverUrlRequest(BaseModel):
    url: str


@router.post("/{work_id}/cover/url")
def set_cover_from_url(work_id: int, body: CoverUrlRequest,
                       db: Session = Depends(get_db)):
    """Download an image URL and embed it into all the work's audio files.
    Also records the url as a MANUAL cover_url provenance row (winner)."""
    import urllib.request
    from audiobiblio.library.cover import embed_cover_for_work, sniff_mime
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(404, "Work not found")
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "url must be http(s)")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(15_000_000)
    except Exception as e:
        raise HTTPException(502, f"download failed: {e}")
    if len(data) < 1000:
        raise HTTPException(422, "downloaded file is too small to be a cover")
    n = embed_cover_for_work(db, work_id, data)
    # additive: one provenance row PER URL (upsert key includes source, so a
    # constant source overwrote the previous candidate — user rule: keep all)
    record_value(db, "work", work_id, "cover_url", url, FieldOrigin.MANUAL, url[:400])
    db.commit()
    from urllib.parse import urlparse as _up
    stored = _store_cover_candidate(db, work_id, data, _up(url).netloc or "url")
    return {"embedded": n, "bytes": len(data), "mime": sniff_mime(data),
            "stored": stored}


from fastapi import File, UploadFile


@router.post("/{work_id}/cover/upload")
async def set_cover_from_upload(work_id: int,
                                file: UploadFile = File(...),
                                db: Session = Depends(get_db)):
    """Embed an uploaded image (drag&drop / file picker) into all the
    work's audio files; records a MANUAL cover_url provenance row."""
    from audiobiblio.library.cover import embed_cover_for_work, sniff_mime
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(404, "Work not found")
    data = await file.read()
    if len(data) < 1000:
        raise HTTPException(422, "file too small to be a cover")
    if len(data) > 15_000_000:
        raise HTTPException(422, "file too large (max 15 MB)")
    n = embed_cover_for_work(db, work_id, data)
    record_value(db, "work", work_id, "cover_url",
                 f"upload:{file.filename}", FieldOrigin.MANUAL,
                 f"upload:{file.filename}")
    db.commit()
    stored = _store_cover_candidate(db, work_id, data, file.filename or "upload")
    return {"embedded": n, "bytes": len(data), "mime": sniff_mime(data),
            "stored": stored}


# ---------------------------------------------------------------------------
# Adopt from disk — the user's manual downloads become the work's files
# ---------------------------------------------------------------------------

_ADOPT_ROOTS = {"/media/fiction", "/media/nonfiction", "/media/audiobooks", "/media/cd.cz"}
_SHARE_TO_MOUNT = {
    "eBOOKs.fiction": "/media/fiction",
    "eBOOKs.nonfiction": "/media/nonfiction",
    "eBOOKs/audiobooks": "/media/audiobooks",
}
_AUDIO_EXTS = {".m4a", ".m4b", ".mp3", ".opus", ".ogg", ".flac", ".aac"}


def _normalize_adopt_dir(raw: str) -> str:
    """Accept container paths, share-style paths and Windows UNC paths."""
    d = raw.strip().replace("\\", "/")
    d = d.split("ebooks/", 1)[-1] if "//10." in d.lower() else d
    for share, mount in _SHARE_TO_MOUNT.items():
        idx = d.find(share)
        if idx != -1:
            return mount + d[idx + len(share):]
    return d


class AdoptRequest(BaseModel):
    directory: str
    dry_run: bool = True


class AdoptResponse(BaseModel):
    actions: list[str]
    applied: bool
    matched: int
    created: int
    errors: list[str]


@router.post("/{work_id}/adopt", response_model=AdoptResponse)
def adopt_from_disk(work_id: int, body: AdoptRequest,
                    db: Session = Depends(get_db)) -> AdoptResponse:
    """Attach the user's manually downloaded files to this work.

    Matches numbered audio files to episodes by part number; creates the
    missing episodes when the index has only a GONE stub (expired serials);
    cancels open download jobs; records final_path so nothing ever moves
    or rewrites the files again.
    """
    import re as _re
    from pathlib import Path as _P

    from audiobiblio.core.db.models import (
        Asset, AssetStatus, AssetType, AvailabilityStatus, DownloadJob,
        Episode, JobStatus,
    )

    work = (
        db.query(Work).options(joinedload(Work.episodes))
        .filter(Work.id == work_id).first()
    )
    if work is None:
        raise HTTPException(404, "Work not found")

    directory = _normalize_adopt_dir(body.directory)
    d = _P(directory)
    if not any(directory.startswith(r) for r in _ADOPT_ROOTS):
        raise HTTPException(422, f"adresar musi lezet v media mountech: {sorted(_ADOPT_ROOTS)}")
    if not d.is_dir():
        raise HTTPException(404, f"adresar neexistuje: {directory}")

    files = sorted(p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() in _AUDIO_EXTS)
    if not files:
        raise HTTPException(422, "v adresari nejsou zadne audio soubory")

    numbered: dict[int, _P] = {}
    for i, f in enumerate(files, 1):
        m = _re.search(r"[-_ ](\d{1,3})\s*$", f.stem) or _re.search(r"^(\d{1,3})[.\-_ ]", f.stem)
        numbered[int(m.group(1)) if m else i] = f

    eps = {e.episode_number: e for e in work.episodes}
    first = min(work.episodes, key=lambda e: e.episode_number or 99, default=None)
    actions: list[str] = []
    errors: list[str] = []
    matched = created = 0

    for n, f in sorted(numbered.items()):
        ep = eps.get(n)
        if ep is None:
            actions.append(f"vytvorit dil {n} (index mel jen stub) + pripojit {f.name}")
            created += 1
        else:
            actions.append(f"dil {n}: pripojit {f.name}")
            matched += 1
        if body.dry_run:
            continue
        if ep is None:
            ep = Episode(
                work_id=work.id, title=first.title if first else work.title,
                url=first.url if first else None, episode_number=n,
                availability_status=AvailabilityStatus.GONE,
                published_at=first.published_at if first else None,
            )
            db.add(ep); db.flush()
            eps[n] = ep
        asset = db.query(Asset).filter_by(episode_id=ep.id, type=AssetType.AUDIO).first()
        if asset is None:
            asset = Asset(episode_id=ep.id, type=AssetType.AUDIO)
            db.add(asset)
        asset.status = AssetStatus.COMPLETE
        asset.file_path = str(f)
        asset.size_bytes = f.stat().st_size
        for j in db.query(DownloadJob).filter(
                DownloadJob.episode_id == ep.id,
                DownloadJob.status.in_([JobStatus.PENDING, JobStatus.APPROVAL])):
            j.status = JobStatus.SKIPPED
            j.reason = "adopted from user's offline copy"

    actions.append(f"final_path -> {directory} (ochrana pred presuny i prepisem tagu)")
    if not body.dry_run:
        if not work.expected_total:
            work.expected_total = len(numbered)
            work.expected_source = "user_offline"
        record_value(db, "work", work.id, "final_path", directory,
                     FieldOrigin.MANUAL, "adopt_from_disk")
        db.commit()

    return AdoptResponse(actions=actions, applied=not body.dry_run,
                         matched=matched, created=created, errors=errors)


@router.post("/{work_id}/sync-tags")
def sync_work_tags(work_id: int, db: Session = Depends(get_db)):
    """Project resolved DB metadata into this work's files NOW.
    Shelved works: only MANUAL values rewrite (hand-made tags protected)."""
    from audiobiblio.library.sync import sync_episode_tags
    work = db.query(Work).options(joinedload(Work.episodes)).filter(
        Work.id == work_id).first()
    if work is None:
        raise HTTPException(404, "Work not found")
    rewrote = protected = 0
    for ep in work.episodes:
        rep = sync_episode_tags(db, ep, write=True)
        for d in rep.diffs:
            if d.action == "rewrite": rewrote += 1
            elif d.action == "protected": protected += 1
    db.commit()
    return {"fields_rewritten": rewrote, "protected": protected,
            "episodes": len(work.episodes)}


class CoverDeleteRequest(BaseModel):
    url: str


@router.post("/{work_id}/cover/delete")
def delete_cover_candidate(work_id: int, body: CoverDeleteRequest,
                           db: Session = Depends(get_db)):
    """Remove a cover candidate: its provenance rows + the stored file in
    _meta/covers. Only an explicit user action removes candidates."""
    from pathlib import Path as _P
    from audiobiblio.core.db.models import MetadataValue
    removed_rows = (
        db.query(MetadataValue)
        .filter_by(entity_type="work", entity_id=work_id, field="cover_url",
                   value=body.url)
        .delete(synchronize_session=False)
    )
    db.commit()
    removed_files = 0
    final = _resolved_final_path(db, work_id)
    if final:
        covers = _P(final) / "_meta" / "covers"
        if covers.is_dir():
            from urllib.parse import urlparse as _up
            netloc = _up(body.url).netloc
            for f in covers.iterdir():
                if netloc and netloc.replace(".", "_") in f.name.replace(".", "_"):
                    try:
                        f.unlink(); removed_files += 1
                    except OSError:
                        pass
    return {"removed_rows": removed_rows, "removed_files": removed_files}
