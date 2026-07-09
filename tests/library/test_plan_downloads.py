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


# ---------------------------------------------------------------------------
# Bug B: plan_downloads must not create duplicate open jobs on re-crawl
# ---------------------------------------------------------------------------

def test_second_plan_skips_assets_with_open_jobs(db_session, episode_factory):
    """A second plan_downloads call while jobs are still APPROVAL must produce
    zero new jobs — the existing open jobs block re-creation.
    """
    ep = episode_factory(program_name="Dedup")
    first_jobs = plan_downloads(db_session, ep.id)
    assert first_jobs, "expected at least one job on first plan"

    # Simulate: all first-round jobs are still open (APPROVAL by default for a fresh program).
    assert all(j.status == JobStatus.APPROVAL for j in first_jobs)

    second_jobs = plan_downloads(db_session, ep.id)
    assert second_jobs == [], (
        f"Expected 0 new jobs (open jobs exist), got {len(second_jobs)}: {second_jobs}"
    )


def test_replan_after_error_creates_new_jobs(db_session, episode_factory):
    """ERROR jobs are treated as 'closed'; a re-plan after error creates new jobs."""
    ep = episode_factory(program_name="Retry")
    first_jobs = plan_downloads(db_session, ep.id)
    assert first_jobs

    # Mark all first-round jobs as ERROR (closed).
    for j in first_jobs:
        j.status = JobStatus.ERROR
    db_session.flush()

    second_jobs = plan_downloads(db_session, ep.id)
    assert second_jobs, "expected new jobs after all previous jobs errored"


def test_replan_after_skipped_creates_new_jobs(db_session, episode_factory):
    """SKIPPED jobs are treated as 'closed'; a re-plan creates new jobs."""
    ep = episode_factory(program_name="Skipped")
    first_jobs = plan_downloads(db_session, ep.id)
    assert first_jobs

    for j in first_jobs:
        j.status = JobStatus.SKIPPED
    db_session.flush()

    second_jobs = plan_downloads(db_session, ep.id)
    assert second_jobs, "expected new jobs after all previous jobs were skipped"
