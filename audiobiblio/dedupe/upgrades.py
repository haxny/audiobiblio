"""Re-air upgrade evaluation (spec §4.2).

Layer: dedupe (layer 4) — imports core only.
"""
from __future__ import annotations

import structlog
from typing import Optional

from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType,
    Episode, UpgradeCandidate, UpgradeStatus,
)
from audiobiblio.core.urls import norm_url as _norm_url

log = structlog.get_logger()

# AD RULE (spec §4.2): duration difference threshold for ad-suspect detection.
# Differences > this value are NEVER auto-resolved — always PENDING_REVIEW.
_AD_SUSPECT_THRESHOLD_MS = 5_000


def evaluate_reair(
    session: Session,
    episode: Episode,
    candidate_url: str,
    candidate_duration_ms: Optional[int],
) -> UpgradeCandidate | None:
    """Evaluate a re-aired URL and create an upgrade candidate when warranted.

    Decision branches (spec §4.2 AD RULE):

    1. No COMPLETE AUDIO asset → return None.
       The normal re-download path handles missing/incomplete assets; no upgrade
       candidate is needed.

    2. Both durations known and abs(diff) <= 5 000 ms → return None.
       Content is the same; adding the alias is sufficient.

    3. Both durations known and abs(diff) > 5 000 ms → create PENDING_REVIEW.
       Ad-suspect pair. NEVER auto-resolved regardless of direction — shorter-but-clean
       beats longer-with-ads, but the human decides. (AD RULE, spec §4.2)

    4. Candidate duration unknown → create PENDING_REVIEW with note "duration unknown".
       Cannot compare; flag for human inspection.

    5. Idempotent: existing (episode_id, candidate_url) row → return it unchanged.

    Owned duration: uses episode.duration_ms (populated by mediainfo, Task 3). Asset
    has no separate duration column; Episode.duration_ms is the authoritative source.

    Args:
        session: SQLAlchemy session (caller commits).
        episode: The existing owned episode being matched.
        candidate_url: The newly discovered URL (will be normalized internally).
        candidate_duration_ms: Duration of the candidate in milliseconds, or None if unknown.

    Returns:
        UpgradeCandidate row, or None if no candidate was warranted.
    """
    norm = _norm_url(candidate_url)

    # Branch 5: idempotency — return existing row unchanged
    existing = (
        session.query(UpgradeCandidate)
        .filter_by(episode_id=episode.id, candidate_url=norm)
        .first()
    )
    if existing:
        log.debug(
            "upgrade_candidate_idempotent",
            episode_id=episode.id,
            candidate_url=norm,
            candidate_id=existing.id,
        )
        return existing

    # Branch 1: owned audio asset must be COMPLETE
    owned_asset = (
        session.query(Asset)
        .filter_by(episode_id=episode.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        .first()
    )
    if not owned_asset:
        log.debug(
            "upgrade_skip_no_complete_asset",
            episode_id=episode.id,
            candidate_url=norm,
        )
        return None

    owned_duration_ms: Optional[int] = episode.duration_ms

    # Branches 2 & 3: both durations known → compare
    if candidate_duration_ms is not None and owned_duration_ms is not None:
        diff = abs(candidate_duration_ms - owned_duration_ms)
        if diff <= _AD_SUSPECT_THRESHOLD_MS:
            # Branch 2: same content; alias only
            log.debug(
                "upgrade_skip_within_tolerance",
                episode_id=episode.id,
                candidate_url=norm,
                diff_ms=diff,
            )
            return None
        # Branch 3: ad-suspect — NEVER auto-resolve
        candidate = UpgradeCandidate(
            episode_id=episode.id,
            candidate_url=norm,
            candidate_duration_ms=candidate_duration_ms,
            owned_duration_ms=owned_duration_ms,
            owned_asset_id=owned_asset.id,
            status=UpgradeStatus.PENDING_REVIEW,
        )
        session.add(candidate)
        session.flush()
        log.info(
            "upgrade_candidate_created",
            episode_id=episode.id,
            candidate_url=norm,
            diff_ms=diff,
            reason="ad_suspect",
        )
        return candidate

    # Branch 4: candidate duration unknown (or owned unknown)
    if candidate_duration_ms is None:
        note = "duration unknown"
    else:
        note = "owned duration unknown"

    candidate = UpgradeCandidate(
        episode_id=episode.id,
        candidate_url=norm,
        candidate_duration_ms=candidate_duration_ms,
        owned_duration_ms=owned_duration_ms,
        owned_asset_id=owned_asset.id,
        status=UpgradeStatus.PENDING_REVIEW,
        note=note,
    )
    session.add(candidate)
    session.flush()
    log.info(
        "upgrade_candidate_created",
        episode_id=episode.id,
        candidate_url=norm,
        reason=note,
    )
    return candidate
