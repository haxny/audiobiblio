"""Tests for the inbox grouping logic.

Route taken: grouping-function test (the brief's fallback option).
Rationale: mounting the full views router in the test app would drag in
config/scheduler dependencies (load_config() and build_paths_for_episode()
both hit disk).  Instead we test the extracted pure-ish function
_group_approval_jobs(db) directly using the existing DB fixtures.
"""
import pytest
from unittest.mock import patch

from audiobiblio.core.db.models import AssetType, DownloadJob, JobStatus
from audiobiblio.web.views import _group_approval_jobs


def _mk_approval_job(db_session, ep):
    job = DownloadJob(
        episode_id=ep.id,
        asset_type=AssetType.AUDIO,
        status=JobStatus.APPROVAL,
    )
    db_session.add(job)
    db_session.flush()
    return job


@pytest.fixture()
def _patch_build_paths():
    """Stub out build_paths_for_episode so tests don't need a real library dir.

    _group_approval_jobs imports it with a local `from ... import` at call time,
    so we patch the function at its definition site in the library module.
    """
    from pathlib import Path
    with patch(
        "audiobiblio.library.pipelines.library.build_paths_for_episode",
        return_value={"base_dir": Path("/lib/prog"), "stem": "ep-1"},
    ) as m:
        yield m


def test_empty_inbox_returns_no_groups(db_session):
    """With no APPROVAL jobs the function returns an empty groups list."""
    groups, total = _group_approval_jobs(db_session)
    assert groups == []
    assert total == 0


def test_groups_by_program(db_session, episode_factory, _patch_build_paths):
    """Two APPROVAL jobs in the same program appear in one group."""
    ep1 = episode_factory(program_name="Test Radio")
    ep2 = episode_factory(program_name="Test Radio")
    j1 = _mk_approval_job(db_session, ep1)
    j2 = _mk_approval_job(db_session, ep2)

    groups, total = _group_approval_jobs(db_session)

    assert total == 2
    assert len(groups) == 1
    assert groups[0]["program_name"] == "Test Radio"
    job_ids = {j.id for j in groups[0]["jobs"]}
    assert j1.id in job_ids
    assert j2.id in job_ids


def test_different_programs_produce_separate_groups(db_session, episode_factory, _patch_build_paths):
    """Jobs from different programs become separate groups, sorted by name."""
    ep_b = episode_factory(program_name="B Program")
    ep_a = episode_factory(program_name="A Program")
    _mk_approval_job(db_session, ep_b)
    _mk_approval_job(db_session, ep_a)

    groups, total = _group_approval_jobs(db_session)

    assert total == 2
    assert len(groups) == 2
    assert groups[0]["program_name"] == "A Program"
    assert groups[1]["program_name"] == "B Program"


def test_non_approval_jobs_excluded(db_session, episode_factory, _patch_build_paths):
    """PENDING and SUCCESS jobs must not appear in the inbox groups."""
    ep1 = episode_factory()
    ep2 = episode_factory()
    _mk_approval_job(db_session, ep1)  # APPROVAL — should appear
    pending = DownloadJob(episode_id=ep2.id, asset_type=AssetType.AUDIO, status=JobStatus.PENDING)
    db_session.add(pending)
    db_session.flush()

    groups, total = _group_approval_jobs(db_session)

    assert total == 1
    assert len(groups) == 1


def test_proposed_path_attached(db_session, episode_factory, _patch_build_paths):
    """Each job should have a proposed_path attribute after grouping."""
    ep = episode_factory(program_name="Path Prog")
    _mk_approval_job(db_session, ep)

    groups, total = _group_approval_jobs(db_session)

    assert total == 1
    job = groups[0]["jobs"][0]
    assert hasattr(job, "proposed_path")
    assert job.proposed_path != "?"
