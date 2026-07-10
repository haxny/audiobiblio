from __future__ import annotations
from typing import Iterable
from sqlalchemy import select, func
from audiobiblio.core.db.models import (
    Asset, AssetType, AssetStatus, Episode, DownloadJob, JobStatus,
    Work, Series, Program, ApprovalMode,
)
from audiobiblio.core.db.session import get_session
from audiobiblio.library.pipelines.completeness import complete_audio_count
import structlog

log = structlog.get_logger()

REQUIRED_ASSETS: list[AssetType] = [AssetType.META_JSON, AssetType.WEBPAGE, AssetType.AUDIO]
APPROVAL_THRESHOLD = 3  # first N jobs per program need manual approval

# Open statuses used by both plan_downloads and dedupe_open_jobs.
_OPEN_STATUSES = (JobStatus.PENDING, JobStatus.APPROVAL, JobStatus.RUNNING, JobStatus.WATCH)

def ensure_assets_for_episode(session, episode_id: int) -> list[Asset]:
    """Upsert required asset rows for an episode and return them."""
    assets = {a.type: a for a in session.scalars(select(Asset).where(Asset.episode_id == episode_id)).all()}
    changed = False
    for t in REQUIRED_ASSETS:
        if t not in assets:
            a = Asset(episode_id=episode_id, type=t, status=AssetStatus.MISSING)
            session.add(a)
            assets[t] = a
            changed = True
    if changed:
        session.commit()
    return list(assets.values())

def _program_has_approved_jobs(session, episode_id: int) -> bool:
    """Check if the program containing this episode already has approved/successful downloads."""
    # Walk episode -> work -> series -> program
    ep = session.get(Episode, episode_id)
    if not ep:
        return True  # safe default: don't require approval for orphaned episodes
    work = session.get(Work, ep.work_id) if ep else None
    series = session.get(Series, work.series_id) if work else None
    if not series:
        return True

    # Count jobs in SUCCESS/PENDING/RUNNING state across this program
    approved_count = (
        session.query(func.count(DownloadJob.id))
        .join(Episode, Episode.id == DownloadJob.episode_id)
        .join(Work, Work.id == Episode.work_id)
        .join(Series, Series.id == Work.series_id)
        .filter(
            Series.program_id == series.program_id,
            DownloadJob.status.in_([
                JobStatus.SUCCESS, JobStatus.PENDING, JobStatus.RUNNING,
            ]),
        )
        .scalar()
    ) or 0
    return approved_count >= APPROVAL_THRESHOLD


def _work_has_gap(session, episode_id: int) -> bool:
    """Return True when the episode's work has expected_total set and have < expected_total.

    'have' = count of distinct episodes in the work with a COMPLETE AUDIO asset.
    Used to tag DownloadJob.reason with 'gap-fill' for inbox discoverability.
    """
    ep = session.get(Episode, episode_id)
    if not ep:
        return False
    work = session.get(Work, ep.work_id)
    if work is None or work.expected_total is None:
        return False

    have = complete_audio_count(session, work.id)
    return have < work.expected_total


def plan_downloads(session, episode_id: int,
                   approval_mode: "ApprovalMode | None" = None) -> list[DownloadJob]:
    """Consult assets and create DownloadJob rows only for what is needed.

    If approval_mode is AUTO, jobs start as PENDING regardless of history.
    If approval_mode is REVIEW, jobs start as APPROVAL regardless of history.
    If approval_mode is None (legacy), threshold logic applies: APPROVAL for new
    programs, PENDING for programs with APPROVAL_THRESHOLD approved downloads.

    Gap-fill tagging: when the episode's work has expected_total set and
    have < expected_total, each created job's reason is appended with
    '; gap-fill' so the Inbox can surface gap-filling activity.
    """
    jobs: list[DownloadJob] = []
    assets = ensure_assets_for_episode(session, episode_id)

    # Determine initial status based on approval_mode or legacy threshold
    if approval_mode is ApprovalMode.AUTO:
        initial_status = JobStatus.PENDING
    elif approval_mode is ApprovalMode.REVIEW:
        initial_status = JobStatus.APPROVAL
    else:
        program_approved = _program_has_approved_jobs(session, episode_id)
        initial_status = JobStatus.PENDING if program_approved else JobStatus.APPROVAL

    gap_fill = _work_has_gap(session, episode_id)

    for a in assets:
        need = a.status in {AssetStatus.MISSING, AssetStatus.STALE, AssetStatus.FAILED}
        if not need:
            continue
        # Skip asset if an open job already exists for (episode_id, asset_type).
        existing_open = session.scalar(
            select(DownloadJob).where(
                DownloadJob.episode_id == episode_id,
                DownloadJob.asset_type == a.type,
                DownloadJob.status.in_(list(_OPEN_STATUSES)),
            )
        )
        if existing_open:
            log.debug(
                "plan_downloads_skip_open_job",
                episode_id=episode_id,
                asset_type=str(a.type),
                existing_job_id=existing_open.id,
            )
            continue
        reason = f"asset:{a.type} status {a.status}"
        if gap_fill:
            reason += "; gap-fill"
        job = DownloadJob(episode_id=episode_id, asset_type=a.type, status=initial_status,
                          reason=reason)
        session.add(job)
        jobs.append(job)
    if jobs:
        session.commit()
        log.info("planned_downloads", episode_id=episode_id, count=len(jobs),
                 status=initial_status.value, gap_fill=gap_fill)
    else:
        log.info("nothing_to_do", episode_id=episode_id)
    return jobs

def mark_asset_complete(session, episode_id: int, asset_type: AssetType, file_path: str,
                        size_bytes: int | None = None, extra: dict | None = None):
    a = session.scalar(
        select(Asset).where(Asset.episode_id == episode_id, Asset.type == asset_type)
    )
    if not a:
        a = Asset(episode_id=episode_id, type=asset_type)
        session.add(a)
    a.status = AssetStatus.COMPLETE
    a.file_path = file_path
    a.size_bytes = size_bytes
    if extra:
        a.extra = (a.extra or {}) | extra
    session.commit()
    log.info("asset_complete", episode_id=episode_id, asset=str(asset_type), path=file_path)


def dedupe_open_jobs(session, dry_run: bool = False) -> int:
    """Find and de-duplicate open DownloadJobs per (episode_id, asset_type).

    Keeps the OLDEST open job (lowest primary key) for each
    (episode_id, asset_type) pair.  All newer duplicates are marked SKIPPED
    with reason "duplicate job cleanup".

    Open statuses considered: PENDING, APPROVAL, RUNNING, WATCH.
    Closed statuses (ERROR, SKIPPED, SUCCESS) are intentionally ignored so
    that closed-job history is preserved and retry semantics are not disturbed.

    Args:
        session: SQLAlchemy session.
        dry_run: When True, count duplicates but do NOT write any changes.

    Returns:
        Number of duplicate jobs that were (or would be) marked SKIPPED.
    """
    open_jobs = session.scalars(
        select(DownloadJob)
        .where(DownloadJob.status.in_(list(_OPEN_STATUSES)))
        .order_by(DownloadJob.id.asc())
    ).all()

    seen: dict[tuple[int, str], int] = {}  # (episode_id, asset_type_str) -> oldest job id
    duplicates: list[DownloadJob] = []

    for job in open_jobs:
        key = (job.episode_id, str(job.asset_type))
        if key not in seen:
            seen[key] = job.id
        else:
            duplicates.append(job)

    if not dry_run:
        for job in duplicates:
            job.status = JobStatus.SKIPPED
            job.reason = "duplicate job cleanup"
        if duplicates:
            session.commit()
            log.info("dedupe_open_jobs", removed=len(duplicates))

    return len(duplicates)
