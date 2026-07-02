"""Characterization: current threshold-based approval in plan_downloads.

First jobs in a fresh program require approval; once a program has
APPROVAL_THRESHOLD jobs in SUCCESS/PENDING/RUNNING, new jobs go straight
to PENDING. Pinned before Task 3 adds the per-target override.
"""
from audiobiblio.core.db.models import AssetType, DownloadJob, JobStatus
from audiobiblio.library.pipelines.checks import APPROVAL_THRESHOLD, plan_downloads


def test_fresh_program_requires_approval(db_session, episode_factory):
    ep = episode_factory(program_name="Fresh")
    jobs = plan_downloads(db_session, ep.id)
    assert jobs, "expected at least one job for a MISSING asset"
    assert all(j.status == JobStatus.APPROVAL for j in jobs)


def test_established_program_auto_pends(db_session, episode_factory):
    # Seed the program with APPROVAL_THRESHOLD successful jobs.
    # We use AssetType.AUDIO for all manually-created jobs.
    for _ in range(APPROVAL_THRESHOLD):
        prior = episode_factory(program_name="Known")
        db_session.add(DownloadJob(episode_id=prior.id, asset_type=AssetType.AUDIO,
                                   status=JobStatus.SUCCESS))
    db_session.flush()
    ep = episode_factory(program_name="Known")
    jobs = plan_downloads(db_session, ep.id)
    assert jobs
    assert all(j.status == JobStatus.PENDING for j in jobs)


from audiobiblio.core.db.models import ApprovalMode


def test_auto_mode_overrides_threshold(db_session, episode_factory):
    ep = episode_factory(program_name="FreshAuto")  # fresh program, no history
    jobs = plan_downloads(db_session, ep.id, approval_mode=ApprovalMode.AUTO)
    assert jobs and all(j.status == JobStatus.PENDING for j in jobs)


def test_review_mode_overrides_established_program(db_session, episode_factory):
    for _ in range(APPROVAL_THRESHOLD):
        prior = episode_factory(program_name="KnownReview")
        db_session.add(DownloadJob(episode_id=prior.id, asset_type=AssetType.AUDIO,
                                   status=JobStatus.SUCCESS))
    db_session.flush()
    ep = episode_factory(program_name="KnownReview")
    jobs = plan_downloads(db_session, ep.id, approval_mode=ApprovalMode.REVIEW)
    assert jobs and all(j.status == JobStatus.APPROVAL for j in jobs)


def test_none_keeps_legacy_threshold(db_session, episode_factory):
    ep = episode_factory(program_name="FreshLegacy")
    jobs = plan_downloads(db_session, ep.id, approval_mode=None)
    assert jobs and all(j.status == JobStatus.APPROVAL for j in jobs)
