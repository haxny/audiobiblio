"""
dedupe.clusters — Surface likely duplicate episodes within the library.

Layer contract
--------------
The ``dedupe`` package sits *below* ``library`` in the import hierarchy
(web → acquire|library → sources|dedupe|tags → core).  Therefore this
module must NOT import from ``audiobiblio.library``.

``merge_episodes`` needs to move a file to trash, which lives in
``library.trash``.  The resolution is dependency injection: the caller
passes a ``trash_fn: Callable[[Path], Path]`` so the *web router*
(top-layer, allowed to import both ``library`` and ``dedupe``) wires the
two together.  The dedupe layer stays layer-clean.

Cluster tiers
-------------
A) Episodes with COMPLETE audio sharing ``norm_url_strip_reair(url)``.
   Grouped in Python after loading candidate rows.
   Note: the unique constraint on (episode_id, type) in Asset makes two
   COMPLETE AUDIO assets per episode impossible, so ``.distinct()`` on the
   Tier-A query is a harmless defensive measure only.
B) Per-program fuzzy title matching with SequenceMatcher ratio > 0.9.
   Generic titles are excluded.  Programs with > 300 episodes are skipped
   (logged) to bound O(n²) cost.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Literal, TypedDict

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    DownloadJob,
    Episode,
    EpisodeAlias,
    FieldOrigin,
    MetadataValue,
    Program,
    Series,
    Work,
)
from audiobiblio.core.urls import norm_url_strip_reair
from audiobiblio.dedupe.matching import _GENERIC_TITLES, _norm_title

log = structlog.get_logger()


class Cluster(TypedDict):
    """A pair (or group) of episodes identified as likely duplicates."""

    key: str
    reason: Literal["same_stripped_url", "fuzzy_title_same_program"]
    episodes: list  # list[Episode]


class ManualMetadataProtectionError(ValueError):
    """Raised by merge_episodes when the duplicate carries MANUAL MetadataValue rows.

    The web router translates this to HTTP 409.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_duplicate_clusters(session: Session, limit: int = 200) -> list[Cluster]:
    """Return up to *limit* clusters of likely duplicate episodes.

    Tier A: Episodes with COMPLETE audio sharing a stripped URL.
    Tier B: Per-program fuzzy title pairs (SequenceMatcher > 0.9).

    Clusters are appended Tier-A first, then Tier-B, and truncated at
    *limit*.  The caller may pass a smaller limit for faster UI responses.
    """
    clusters: list[Cluster] = []

    # ------------------------------------------------------------------
    # Tier A — shared norm_url_strip_reair among COMPLETE-audio episodes
    # ------------------------------------------------------------------
    # .distinct() is a harmless defence; the unique constraint on
    # (episode_id, type) already prevents two COMPLETE AUDIO rows per episode.
    episodes_with_audio: list[Episode] = (
        session.query(Episode)
        .join(
            Asset,
            (Asset.episode_id == Episode.id)
            & (Asset.type == AssetType.AUDIO)
            & (Asset.status == AssetStatus.COMPLETE),
        )
        .distinct()
        .all()
    )

    url_groups: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes_with_audio:
        key = norm_url_strip_reair(ep.url)
        if key:
            url_groups[key].append(ep)

    for key, eps in url_groups.items():
        if len(eps) > 1:
            clusters.append(
                {"key": key, "reason": "same_stripped_url", "episodes": eps}
            )
            if len(clusters) >= limit:
                return clusters

    # ------------------------------------------------------------------
    # Tier B — fuzzy title matching within each program
    # ------------------------------------------------------------------
    programs: list[Program] = session.query(Program).all()

    for program in programs:
        total_eps: int = (
            session.query(Episode)
            .join(Work, Work.id == Episode.work_id)
            .join(Series, Series.id == Work.series_id)
            .filter(Series.program_id == program.id)
            .count()
        )

        if total_eps > 300:
            log.info(
                "tier_b_skip_large_program",
                program_id=program.id,
                program_name=program.name,
                episode_count=total_eps,
            )
            continue

        if total_eps < 2:
            continue

        eps: list[Episode] = (
            session.query(Episode)
            .join(Work, Work.id == Episode.work_id)
            .join(Series, Series.id == Work.series_id)
            .filter(Series.program_id == program.id)
            .all()
        )

        # Build (episode, normalised_title) pairs, dropping generic/empty titles
        norms: list[tuple[Episode, str]] = [
            (ep, n)
            for ep in eps
            if (n := _norm_title(ep.title)) and n not in _GENERIC_TITLES
        ]

        for i, (ep_a, norm_a) in enumerate(norms):
            for ep_b, norm_b in norms[i + 1 :]:
                if SequenceMatcher(None, norm_a, norm_b).ratio() > 0.9:
                    key = f"{ep_a.id}~{ep_b.id}"
                    clusters.append(
                        {
                            "key": key,
                            "reason": "fuzzy_title_same_program",
                            "episodes": [ep_a, ep_b],
                        }
                    )
                    if len(clusters) >= limit:
                        return clusters

    return clusters


def merge_episodes(
    session: Session,
    canonical_id: int,
    duplicate_id: int,
    library_dir: Path,
    dry_run: bool = True,
    trash_fn: Callable[[Path], Path] | None = None,
) -> list[str]:
    """Merge *duplicate_id* into *canonical_id*.

    Returns a list of human-readable action strings describing what was (or
    would be in dry_run mode) performed.

    Args:
        session: SQLAlchemy session.
        canonical_id: Episode to keep.
        duplicate_id: Episode to remove.
        library_dir: Root library directory (informational; real file ops use trash_fn).
        dry_run: When True (default), compute and return the action list without
            touching the DB or filesystem.
        trash_fn: Callable ``(path: Path) -> Path`` to move a file to trash.
            Must be supplied when ``dry_run=False``; injected by the web router
            (see module docstring on the layer contract).

    Raises:
        ManualMetadataProtectionError: duplicate has MANUAL MetadataValue rows.
        ValueError: canonical and duplicate must differ; or episode not found; or
            trash_fn missing when dry_run=False.

    Note on operation ordering:
        The audio file is moved to trash before the DB commit.  This means the
        file is recoverable from trash if the commit fails (deliberate trade-off
        per spec: prefer orphaned-but-recoverable trash entry over a committed
        DB deletion pointing to a missing file).
    """
    # Guard — self-merge is always an error
    if canonical_id == duplicate_id:
        raise ValueError("canonical and duplicate must differ")

    # Guard — refuse if duplicate carries hand-curated metadata
    manual_count: int = (
        session.query(MetadataValue)
        .filter(
            MetadataValue.entity_type == "episode",
            MetadataValue.entity_id == duplicate_id,
            MetadataValue.origin == FieldOrigin.MANUAL,
        )
        .count()
    )
    if manual_count > 0:
        raise ManualMetadataProtectionError(
            f"Episode {duplicate_id} has {manual_count} MANUAL metadata row(s); "
            "merge refused to protect curated data."
        )

    canonical = session.get(Episode, canonical_id)
    duplicate = session.get(Episode, duplicate_id)
    if canonical is None:
        raise ValueError(f"Canonical episode {canonical_id} not found")
    if duplicate is None:
        raise ValueError(f"Duplicate episode {duplicate_id} not found")

    actions: list[str] = []

    # 1. Alias for duplicate's primary URL
    if duplicate.url:
        actions.append(
            f"add alias url={duplicate.url!r} to episode {canonical_id}"
        )

    # 1b. Re-point duplicate's existing EpisodeAlias rows to canonical
    dup_aliases = (
        session.query(EpisodeAlias)
        .filter(EpisodeAlias.episode_id == duplicate_id)
        .all()
    )
    for alias in dup_aliases:
        actions.append(
            f"re-point alias id={alias.id} url={alias.url!r} "
            f"from episode {duplicate_id} to {canonical_id}"
        )

    # 2. Audio file → trash
    audio_asset = (
        session.query(Asset)
        .filter(
            Asset.episode_id == duplicate_id,
            Asset.type == AssetType.AUDIO,
        )
        .first()
    )
    if audio_asset and audio_asset.file_path:
        actions.append(f"trash audio file {audio_asset.file_path!r}")

    # 3. Delete all assets of duplicate
    assets = session.query(Asset).filter(Asset.episode_id == duplicate_id).all()
    for asset in assets:
        actions.append(f"delete asset id={asset.id} type={asset.type}")

    # 4. Delete download jobs
    jobs = session.query(DownloadJob).filter(DownloadJob.episode_id == duplicate_id).all()
    for job in jobs:
        actions.append(f"delete download_job id={job.id}")

    # 5. Delete episode row
    actions.append(
        f"delete episode id={duplicate_id} title={duplicate.title!r}"
    )

    if not dry_run:
        if trash_fn is None:
            raise ValueError(
                "trash_fn must be provided when dry_run=False"
            )

        # 1. Add alias on canonical for duplicate's primary URL
        if duplicate.url:
            existing_alias = (
                session.query(EpisodeAlias)
                .filter(
                    EpisodeAlias.episode_id == canonical_id,
                    EpisodeAlias.url == duplicate.url,
                )
                .first()
            )
            if not existing_alias:
                session.add(
                    EpisodeAlias(
                        episode_id=canonical_id,
                        url=duplicate.url,
                        discovery_source="dedupe_merge",
                    )
                )

        # 1b. Re-point duplicate's existing EpisodeAlias rows to canonical,
        #     dropping any that would collide with an alias already on canonical.
        #     Use the ORM relationship (alias.episode = canonical) so SQLAlchemy
        #     updates both the FK column and the relationship collections correctly,
        #     avoiding a spurious SET NULL when the episode is later deleted.
        for alias in dup_aliases:
            canonical_has_url = (
                session.query(EpisodeAlias)
                .filter(
                    EpisodeAlias.episode_id == canonical_id,
                    EpisodeAlias.url == alias.url,
                )
                .first()
            )
            if canonical_has_url is None:
                alias.episode = canonical
            else:
                session.delete(alias)

        # 2. Trash the audio file
        if audio_asset and audio_asset.file_path:
            audio_path = Path(audio_asset.file_path)
            if audio_path.exists():
                trash_fn(audio_path)

        # 3+4. Delete assets and jobs
        for asset in assets:
            session.delete(asset)
        for job in jobs:
            session.delete(job)

        # 5. Delete episode
        session.delete(duplicate)
        session.commit()

    return actions
