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


# ---------------------------------------------------------------------------
# Cascade tests (Step 1 — RED before implementation)
# ---------------------------------------------------------------------------

def _mk_episode_jobs(db_session, episode_factory, statuses=("APPROVAL",) * 3):
    from audiobiblio.core.db.models import AssetType
    ep = episode_factory()
    types = [AssetType.AUDIO, AssetType.META_JSON, AssetType.WEBPAGE]
    jobs = []
    for t, st in zip(types, statuses):
        j = DownloadJob(episode_id=ep.id, asset_type=t, status=JobStatus[st])
        db_session.add(j)
        jobs.append(j)
    db_session.flush()
    return ep, jobs


def test_approve_cascades_to_sibling_jobs(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{jobs[0].id}/approve")
    assert r.status_code == 200
    assert r.json()["cascaded"] == 3
    for j in jobs:
        db_session.expire(j)
        assert j.status == JobStatus.PENDING


def test_reject_cascades_to_sibling_jobs(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{jobs[1].id}/reject")
    assert r.status_code == 200
    assert r.json()["cascaded"] == 3
    for j in jobs:
        db_session.expire(j)
        assert j.status == JobStatus.SKIPPED


def test_cascade_skips_non_approval_siblings(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory,
                                statuses=("APPROVAL", "SUCCESS", "APPROVAL"))
    r = client.post(f"/api/v1/jobs/{jobs[0].id}/approve")
    assert r.json()["cascaded"] == 2
    db_session.expire(jobs[1])
    assert jobs[1].status == JobStatus.SUCCESS  # untouched
