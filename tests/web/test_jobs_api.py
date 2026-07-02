"""Test reject endpoints for approval inbox."""
from datetime import datetime

from audiobiblio.core.db.models import AssetType, DownloadJob, JobStatus


def _mk_approval_job(db_session, episode_factory):
    """Create an APPROVAL job for testing."""
    ep = episode_factory()
    job = DownloadJob(
        episode_id=ep.id,
        asset_type=AssetType.AUDIO,
        status=JobStatus.APPROVAL,
    )
    db_session.add(job)
    db_session.flush()
    return job


def test_reject_sets_skipped(client, db_session, episode_factory):
    job = _mk_approval_job(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{job.id}/reject")
    assert r.status_code == 200
    db_session.expire(job)
    assert job.status == JobStatus.SKIPPED
    assert "reject" in (job.reason or "").lower()


def test_reject_non_approval_conflicts(client, db_session, episode_factory):
    job = _mk_approval_job(db_session, episode_factory)
    job.status = JobStatus.SUCCESS
    db_session.flush()
    assert client.post(f"/api/v1/jobs/{job.id}/reject").status_code == 409


def test_reject_all(client, db_session, episode_factory):
    for _ in range(3):
        _mk_approval_job(db_session, episode_factory)
    r = client.post("/api/v1/jobs/reject-all")
    assert r.status_code == 200
    assert r.json()["rejected"] == 3
