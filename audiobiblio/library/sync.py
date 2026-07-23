"""sync — DB-resolved tags projected onto episode audio files.

The database is the source of truth (spec §2). File tags are projections.
This module computes the effective resolved value for each sync field and
optionally rewrites the file to match.

Tier: library (tier 3) → tags (tier 4) downward ✓, core (tier 5) downward ✓.

Precedence: MANUAL > ENRICHED > FILE > SCRAPED (from provenance.py).

Decision loop per field:
  1. file == resolved → action "none"
  2. file differs from resolved and file value is non-empty:
       record FILE observation (rank FILE > SCRAPED may flip the winner)
       recompute resolved
       if new_resolved == file_value → action "record_file" (file already wins)
       else → action "rewrite"
  3. action "rewrite" is applied only when write=True via write_tags.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Episode,
    FieldOrigin,
    MetadataValue,
    Work,
)
from audiobiblio.core.provenance import record_value, resolve_field, WORK_FIELDS
from audiobiblio.dedupe.matching import is_generic_title
from audiobiblio.tags.reader import read_tags
from audiobiblio.tags.writer import write_tags

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------

# DB canonical field name → tag key as returned by read_tags / expected by write_tags.
# Keep tight: only fields with clear DB ↔ file correspondences.
DB_TO_TAG: dict[str, str] = {
    "title":       "title",     # track-level: track_tags["title"]
    "author":      "artist",    # album-level: album_tags["artist"] + album_tags["albumartist"]
    "narrator":    "performer", # album-level: album_tags["performer"]
    "genre":       "genre",     # album-level: album_tags["genre"]
    "description": "comment",   # album-level: album_tags["comment"] (iTunes comment atom)
    "year":        "date",      # album-level: album_tags["date"]
}

# Inverse: tag key → DB canonical field name.  Unmapped tag keys are not synced.
TAG_TO_DB: dict[str, str] = {v: k for k, v in DB_TO_TAG.items()}

# WORK_FIELDS is the authoritative entity-routing map, defined in core.provenance.
# Imported here so sync + callers all reference the same constant.
# genre is NOT in WORK_FIELDS — it lives on the episode entity.


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDiff:
    """Diff for one metadata field between the file and the DB-resolved value."""

    field: str
    """DB canonical field name."""

    file_value: str
    """Value read from the audio file tags (empty string if absent)."""

    resolved_value: str
    """Final resolved value from DB provenance after any FILE observation is recorded."""

    action: str
    """
    "none"        — file already matches resolved; no change.
    "record_file" — FILE observation was recorded and the file value won; no rewrite.
    "rewrite"     — resolved differs from file; file needs updating.
    """


@dataclass(frozen=True)
class SyncReport:
    """Result of syncing one episode's tags against DB-resolved values."""

    episode_id: int
    diffs: tuple[FieldDiff, ...]
    note: str = ""
    write_error: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _entity_coords(episode: Episode, db_field: str) -> tuple[str, int]:
    """Return (entity_type, entity_id) for the given DB field."""
    if db_field in WORK_FIELDS:
        return "work", episode.work_id
    return "episode", episode.id


def _get_candidates(
    session, entity_type: str, entity_id: int, db_field: str
) -> list[MetadataValue]:
    return (
        session.query(MetadataValue)
        .filter_by(entity_type=entity_type, entity_id=entity_id, field=db_field)
        .all()
    )


def _orm_fallback(episode: Episode, work: Optional[Work], db_field: str) -> str:
    """Return the ORM-level value for a field when no MetadataValue rows exist."""
    if db_field == "title":
        return episode.title or ""
    if db_field == "author":
        return (work.author if work else None) or ""
    if db_field == "narrator":
        return ""  # No ORM field for narrator
    if db_field == "genre":
        return ""  # Genre lives in Program, not directly in Work
    if db_field == "description":
        return episode.summary or ""
    if db_field == "year":
        if work and work.year:
            return str(work.year)
        if episode.published_at:
            return str(episode.published_at.year)
        return ""
    return ""


def _resolve_one(
    session, episode: Episode, work: Optional[Work], db_field: str
) -> str:
    """Resolve a single field from MetadataValue rows + ORM fallback."""
    entity_type, entity_id = _entity_coords(episode, db_field)
    candidates = _get_candidates(session, entity_type, entity_id, db_field)
    winner = resolve_field(candidates)
    if winner is not None:
        return winner.value or ""
    return _orm_fallback(episode, work, db_field)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def compute_resolved(session, episode: Episode) -> dict[str, str]:
    """Compute the resolved metadata value for each sync field.

    Gathers MetadataValue rows for both the episode and its work (entity
    routing via WORK_FIELDS), resolves via resolve_field, and falls back to
    ORM values where no provenance rows exist.

    Returns a dict keyed by DB canonical field names.
    """
    work: Optional[Work] = session.get(Work, episode.work_id)
    return {
        db_field: _resolve_one(session, episode, work, db_field)
        for db_field in DB_TO_TAG
    }


def sync_episode_tags(
    session, episode: Episode, write: bool = False
) -> SyncReport:
    """Sync DB-resolved metadata onto the episode's COMPLETE audio file.

    Returns a SyncReport even when the episode has no COMPLETE audio file
    (empty diffs + note). Never raises on missing file.

    FILE observations are recorded and flushed to the session in all modes
    (dry-run and write), but persist only when the caller commits.
    The CLI --write flag triggers the commit; dry-run mode does not commit.

    Args:
        session: SQLAlchemy session (no commit — caller owns the transaction).
        episode: Episode ORM object.
        write:   If True, apply "rewrite" actions via write_tags.
                 Default False (dry-run).
    """
    # Locate COMPLETE audio asset
    asset: Optional[Asset] = (
        session.query(Asset)
        .filter_by(episode_id=episode.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        .first()
    )
    if asset is None or not asset.file_path:
        return SyncReport(episode_id=episode.id, diffs=(), note="no COMPLETE audio asset")

    file_path = asset.file_path

    if not Path(file_path).exists():
        log.warning("sync_file_missing", episode_id=episode.id, path=file_path)
        return SyncReport(
            episode_id=episode.id,
            diffs=(),
            note=f"audio file missing: {file_path}",
        )

    # Read current file tags
    try:
        file_tags: dict = read_tags(file_path)
    except Exception as exc:
        log.warning("sync_read_tags_failed", episode_id=episode.id, error=str(exc))
        return SyncReport(
            episode_id=episode.id,
            diffs=(),
            note=f"could not read tags: {exc}",
        )

    # Compute initial resolved values from DB early (needed for M4A guard)
    work: Optional[Work] = session.get(Work, episode.work_id)

    # Curated-shelf guard (user rule 2026-07-24: "pozor na to, at pri
    # enrichmentu neprepises data v souborech, ve kterych uz jsme si tu
    # praci dali rucne"): once a work sits on the curated shelf
    # (final_path), its files are hand-made — only an explicit MANUAL
    # value may rewrite a tag; ENRICHED/SCRAPED winners are recorded in
    # the DB but never projected onto shelved files.
    shelved = False
    if work is not None:
        fp_rows = _get_candidates(session, "work", work.id, "final_path")
        shelved = resolve_field(fp_rows) is not None
    resolved: dict[str, str] = {
        db_field: _resolve_one(session, episode, work, db_field)
        for db_field in DB_TO_TAG
    }

    # Guard: M4A/M4B/MP4 files without exiftool return only freeform atoms,
    # leaving standard tags (title/artist/date/comment) empty. Syncing would
    # rewrite these from DB, destroying any file-side edits. Skip if we would
    # try to sync these fields but can't read them.
    suffix = Path(file_path).suffix.lower()
    if suffix in (".m4a", ".m4b", ".mp4"):
        has_standard_tag = any(
            file_tags.get(k) for k in ("title", "artist", "date", "comment")
        )
        # Check if DB has any values for the standard fields that would trigger syncing
        has_db_standard = any(resolved.get(k) for k in ("title", "author", "year", "description"))

        if not has_standard_tag and has_db_standard:
            log.warning(
                "sync_m4a_tags_unreadable",
                episode_id=episode.id,
                path=str(file_path),
            )
            return SyncReport(
                episode_id=episode.id,
                diffs=(),
                note="could not read M4A standard tags (exiftool missing?) — sync skipped",
            )

    diffs_list: list[FieldDiff] = []

    for db_field, tag_key in DB_TO_TAG.items():
        resolved_value = resolved[db_field]
        file_value = str(file_tags.get(tag_key) or "")

        # --- Case 1: file already matches resolved ---
        if file_value == resolved_value:
            diffs_list.append(FieldDiff(
                field=db_field,
                file_value=file_value,
                resolved_value=resolved_value,
                action="none",
            ))
            continue

        # --- Case 2: values differ ---
        entity_type, entity_id = _entity_coords(episode, db_field)

        if file_value and not (db_field == "title" and is_generic_title(file_value)):
            # Record the file's value as a FILE-origin observation (upsert).
            # file_path is the provenance source, making it unique per file.
            #
            # Guard: skip recording when the title is a known generic placeholder
            # (e.g. "Epizody pořadu").  FILE rank > SCRAPED, so recording a generic
            # title would silently defeat an enriched SCRAPED title and make the
            # placeholder the permanent winner.  Skipping falls through to the
            # "rewrite" case, so the enriched DB value overwrites the generic tag.
            record_value(
                session,
                entity_type=entity_type,
                entity_id=entity_id,
                field=db_field,
                value=file_value,
                origin=FieldOrigin.FILE,
                source=file_path,
            )
            session.flush()

            # Recompute after recording: FILE obs may now win over SCRAPED
            candidates = _get_candidates(session, entity_type, entity_id, db_field)
            winner = resolve_field(candidates)
            new_resolved = (winner.value or "") if winner else resolved_value

            if new_resolved == file_value:
                # FILE observation wins — file already has the right value
                diffs_list.append(FieldDiff(
                    field=db_field,
                    file_value=file_value,
                    resolved_value=new_resolved,
                    action="record_file",
                ))
                continue

            resolved_value = new_resolved

        # --- Case 3: rewrite needed (unless the shelf guard vetoes) ---
        if shelved:
            w_candidates = _get_candidates(session, entity_type, entity_id, db_field)
            w_winner = resolve_field(w_candidates)
            if w_winner is None or w_winner.origin != FieldOrigin.MANUAL:
                diffs_list.append(FieldDiff(
                    field=db_field,
                    file_value=file_value,
                    resolved_value=resolved_value,
                    action="protected",
                ))
                continue
        diffs_list.append(FieldDiff(
            field=db_field,
            file_value=file_value,
            resolved_value=resolved_value,
            action="rewrite",
        ))

    # Apply rewrites if requested
    write_error = ""
    if write:
        rewrite_map = {d.field: d.resolved_value for d in diffs_list if d.action == "rewrite"}
        if rewrite_map:
            write_error = _apply_rewrite(file_path, rewrite_map, file_tags)

    return SyncReport(
        episode_id=episode.id,
        diffs=tuple(diffs_list),
        write_error=write_error,
    )


def _apply_rewrite(
    file_path: str,
    rewrite_fields: dict[str, str],
    file_tags: dict,
) -> str:
    """Apply resolved values to the file, preserving all non-sync tags.

    Reads existing tag values from file_tags for fields not being rewritten
    so that write_tags does not clear them.

    Returns: empty string on success, error message on failure.
    """
    # Start from current file tag values (preserves publisher, tracknumber, www, etc.)
    album_tags: dict[str, str] = {
        "album":       str(file_tags.get("album") or ""),
        "artist":      str(file_tags.get("artist") or ""),
        "albumartist": str(file_tags.get("albumartist") or ""),
        "performer":   str(file_tags.get("performer") or ""),
        "genre":       str(file_tags.get("genre") or ""),
        "date":        str(file_tags.get("date") or ""),
        "comment":     str(file_tags.get("comment") or ""),
        "publisher":   str(file_tags.get("publisher") or ""),
        "www":         str(file_tags.get("www") or ""),
    }
    track_tags: dict[str, str] = {
        "title":       str(file_tags.get("title") or ""),
        "tracknumber": str(file_tags.get("tracknumber") or ""),
    }

    # Override only the fields being rewritten
    for db_field, resolved_val in rewrite_fields.items():
        tag_key = DB_TO_TAG[db_field]
        if tag_key == "title":
            track_tags["title"] = resolved_val
        elif tag_key == "artist":
            album_tags["artist"] = resolved_val
            album_tags["albumartist"] = resolved_val
        else:
            album_tags[tag_key] = resolved_val

    try:
        write_tags(file_path, album_tags, track_tags)
        log.info("sync_rewrite_applied", path=file_path, fields=list(rewrite_fields))
        return ""
    except Exception as exc:
        error_msg = f"write_tags failed: {exc}"
        log.error("sync_rewrite_failed", path=file_path, error=error_msg)
        return error_msg
