"""
audiobiblio.library.enrich_meta
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Enrich episode metadata from the downloaded .info.json (yt-dlp metadata dump).

The user's episodes often show "Episode 9" while the real title sits unused in
the .info.json file.  This module reads that file and applies the richer values
to the ORM episode row, recording SCRAPED provenance for every surviving
candidate so that the provenance model remains consistent.

Public API
----------
enrich_episode_from_meta(session, episode) -> EnrichReport
    Locates the episode's COMPLETE META_JSON asset; extracts title/description/
    duration/episode_number; applies per-field update rules; returns a frozen
    EnrichReport.  Never raises — errors are caught and reflected in the note.

Per-field rules (spec §Task-1):
  title:
    - Skip if is_generic_title(candidate)
    - Skip if has_manual(episode, "title")
    - Update ORM when current title matches ^Episode \\d+$ OR (candidate is longer AND is a prefix-extension of current title)
    - ALWAYS record_value(SCRAPED, source="meta_json") for surviving candidates
  description (summary):
    - Set only when ep.summary is empty / None
  duration_ms:
    - Set only when ep.duration_ms is None
  episode_number:
    - Set only when ep.episode_number is None and source provides a real int
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, FieldOrigin
from audiobiblio.core.provenance import has_manual, record_value
from audiobiblio.dedupe.matching import is_generic_title

log = structlog.get_logger()

_FALLBACK_PATTERN = re.compile(r"^Episode\s+\d+$", re.IGNORECASE)
# MD5 / SHA-1 / SHA-256 hex digests appearing as titles (yt-dlp uses ID hashes sometimes)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{32,64}$", re.IGNORECASE)


@dataclass(frozen=True)
class EnrichReport:
    """Result of one enrich_episode_from_meta call."""

    fields_updated: tuple[str, ...] = field(default_factory=tuple)
    skipped: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


def _best_title(data: dict) -> str | None:
    """Return the best available title: prefer fulltitle when strictly longer."""
    title = (data.get("title") or "").strip() or None
    fulltitle = (data.get("fulltitle") or "").strip() or None
    if fulltitle and title and len(fulltitle) > len(title):
        return fulltitle
    return title or fulltitle


def enrich_episode_from_meta(session, episode, *, dry_run: bool = False) -> EnrichReport:
    """Enrich *episode* from its COMPLETE META_JSON asset.

    Parameters
    ----------
    session:
        SQLAlchemy session. Commits the session when any fields are updated.
        We call session.flush() so IDs are visible within the same transaction.
    episode:
        An ORM Episode instance.
    dry_run:
        If True, compute what would change but write nothing (ORM or provenance).
    """
    fields_updated: list[str] = []
    skipped: list[str] = []

    # Locate COMPLETE META_JSON asset
    asset: Optional[Asset] = (
        session.query(Asset)
        .filter_by(
            episode_id=episode.id,
            type=AssetType.META_JSON,
            status=AssetStatus.COMPLETE,
        )
        .first()
    )

    if asset is None or not asset.file_path:
        return EnrichReport(note="no complete META_JSON asset with file_path")

    jpath = Path(asset.file_path)
    if not jpath.exists():
        log.warning("enrich_meta.file_missing", path=str(jpath), episode_id=episode.id)
        return EnrichReport(note=f"META_JSON file missing: {jpath}")

    # Parse JSON — tolerant
    try:
        data = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        log.warning("enrich_meta.json_parse_error", path=str(jpath), err=str(exc))
        return EnrichReport(note=f"malformed JSON: {exc}")

    if not isinstance(data, dict):
        return EnrichReport(note="malformed JSON: root is not an object")

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------
    title_candidate = _best_title(data)
    if title_candidate:
        if is_generic_title(title_candidate):
            skipped.append("title")
            log.debug("enrich_meta.title_generic", episode_id=episode.id, candidate=title_candidate)
        elif _HASH_PATTERN.match(title_candidate):
            skipped.append("title")
            log.debug("enrich_meta.title_hash", episode_id=episode.id, candidate=title_candidate[:12])
        elif has_manual(session, "episode", episode.id, "title"):
            skipped.append("title")
            log.debug("enrich_meta.title_manual_protected", episode_id=episode.id)
        else:
            # Always record provenance for surviving candidates
            if not dry_run:
                record_value(
                    session,
                    entity_type="episode",
                    entity_id=episode.id,
                    field="title",
                    value=title_candidate,
                    origin=FieldOrigin.SCRAPED,
                    source="meta_json",
                )
            # Decide whether to update ORM
            current = episode.title or ""
            is_fallback = bool(_FALLBACK_PATTERN.match(current))
            # "candidate longer" only triggers when the candidate genuinely
            # extends the current title (prefix relationship) or when the current
            # is empty.  This prevents series-level titles from overwriting good
            # episode-specific titles just because they happen to be longer.
            current_lower = current.lower()
            cand_lower = title_candidate.lower()
            is_extension = (
                not current_lower  # current is empty
                or cand_lower.startswith(current_lower)  # candidate is a longer version of current
                or current_lower.startswith(cand_lower[:max(1, len(cand_lower) - 3)])
            )
            is_longer = len(title_candidate) > len(current) and is_extension
            if is_fallback or is_longer:
                if not dry_run:
                    episode.title = title_candidate
                    session.flush()
                fields_updated.append("title")
                log.info("enrich_meta.title_updated",
                         episode_id=episode.id,
                         old=current,
                         new=title_candidate)

    # ------------------------------------------------------------------
    # Description → episode.summary (set only when empty)
    # ------------------------------------------------------------------
    description = (data.get("description") or "").strip() or None
    if description and not episode.summary:
        if not dry_run:
            episode.summary = description
            record_value(
                session,
                entity_type="episode",
                entity_id=episode.id,
                field="summary",
                value=description,
                origin=FieldOrigin.SCRAPED,
                source="meta_json",
            )
            session.flush()
        fields_updated.append("summary")

    # ------------------------------------------------------------------
    # duration_ms (set only when NULL)
    # ------------------------------------------------------------------
    raw_duration = data.get("duration")
    if raw_duration is not None and episode.duration_ms is None:
        try:
            duration_ms = int(float(raw_duration) * 1000)
            if duration_ms > 0:
                if not dry_run:
                    episode.duration_ms = duration_ms
                    session.flush()
                fields_updated.append("duration_ms")
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------
    # episode_number (set only when NULL, source provides a real int)
    # ------------------------------------------------------------------
    ep_num_raw = data.get("episode") or data.get("track")
    if ep_num_raw is not None and episode.episode_number is None:
        try:
            ep_num = int(ep_num_raw)
            if ep_num > 0:
                if not dry_run:
                    episode.episode_number = ep_num
                    session.flush()
                fields_updated.append("episode_number")
        except (TypeError, ValueError):
            pass

    if not dry_run and fields_updated:
        session.commit()

    return EnrichReport(
        fields_updated=tuple(fields_updated),
        skipped=tuple(skipped),
    )
