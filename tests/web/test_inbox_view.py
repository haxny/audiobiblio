"""Tests for the inbox grouping logic.

Route taken: grouping-function test (the brief's fallback option).
Rationale: mounting the full views router in the test app would drag in
config/scheduler dependencies (load_config() and build_paths_for_episode()
both hit disk).  Instead we test the extracted pure-ish function
_group_approval_jobs(db) directly using the existing DB fixtures.

After the per-episode reshape (Phase 3 Task 1) each group has an ``episodes``
list (one dict per episode), not a flat ``jobs`` list.  Each episode dict
carries ``job_ids`` and ``asset_types`` so the template can render all assets
for that episode in a single row and target any one job ID for approve/reject
cascade.
"""
import pytest
from unittest.mock import patch

from audiobiblio.core.db.models import (
    AssetType, DownloadJob, JobStatus,
    UpgradeCandidate, UpgradeStatus,
)
from audiobiblio.web.views import _group_approval_jobs, _query_upgrade_candidates, _fmt_duration_ms


def _mk_approval_job(db_session, ep, asset_type=AssetType.AUDIO):
    job = DownloadJob(
        episode_id=ep.id,
        asset_type=asset_type,
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
    """Two APPROVAL jobs for two episodes in the same program → one group, two episode rows."""
    ep1 = episode_factory(program_name="Test Radio")
    ep2 = episode_factory(program_name="Test Radio")
    j1 = _mk_approval_job(db_session, ep1)
    j2 = _mk_approval_job(db_session, ep2)

    groups, total = _group_approval_jobs(db_session)

    assert total == 2
    assert len(groups) == 1
    assert groups[0]["program_name"] == "Test Radio"
    episodes = groups[0]["episodes"]
    assert len(episodes) == 2
    all_job_ids = {jid for ep in episodes for jid in ep["job_ids"]}
    assert j1.id in all_job_ids
    assert j2.id in all_job_ids


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
    """Each episode entry should have a proposed_path after grouping."""
    ep = episode_factory(program_name="Path Prog")
    _mk_approval_job(db_session, ep)

    groups, total = _group_approval_jobs(db_session)

    assert total == 1
    ep_entry = groups[0]["episodes"][0]
    assert "proposed_path" in ep_entry
    assert ep_entry["proposed_path"] != "?"


def test_sibling_jobs_merged_into_one_episode_row(db_session, episode_factory, _patch_build_paths):
    """Multiple APPROVAL jobs for the same episode appear as one episode entry."""
    ep = episode_factory(program_name="Merge Prog")
    j_audio = _mk_approval_job(db_session, ep, AssetType.AUDIO)
    j_meta = _mk_approval_job(db_session, ep, AssetType.META_JSON)
    j_web = _mk_approval_job(db_session, ep, AssetType.WEBPAGE)

    groups, total = _group_approval_jobs(db_session)

    assert total == 3
    assert len(groups) == 1
    episodes = groups[0]["episodes"]
    assert len(episodes) == 1, "Three sibling jobs must collapse into one episode row"
    ep_entry = episodes[0]
    assert set(ep_entry["job_ids"]) == {j_audio.id, j_meta.id, j_web.id}
    assert set(ep_entry["asset_types"]) == {"audio", "meta_json", "webpage"}


# ---------------------------------------------------------------------------
# Priority ordering tests
# ---------------------------------------------------------------------------


def test_inbox_ordered_priority_desc_id_asc(db_session, episode_factory, _patch_build_paths):
    """Episodes within a group are ordered priority DESC, id ASC.

    Episode with higher priority should come first even if its DB id is larger.
    """
    ep_low = episode_factory(program_name="Priority Prog")
    ep_high = episode_factory(program_name="Priority Prog")

    # ep_low has id < ep_high (created first), but give ep_high higher priority
    ep_low.priority = 0
    ep_high.priority = 10
    db_session.flush()

    _mk_approval_job(db_session, ep_low)
    _mk_approval_job(db_session, ep_high)

    groups, total = _group_approval_jobs(db_session)

    assert total == 2
    assert len(groups) == 1
    episodes = groups[0]["episodes"]
    assert len(episodes) == 2
    # High-priority episode must come first
    assert episodes[0]["id"] == ep_high.id
    assert episodes[1]["id"] == ep_low.id


def test_inbox_same_priority_ordered_by_id_asc(db_session, episode_factory, _patch_build_paths):
    """When priority is equal, episodes are ordered by id ASC (stable FIFO)."""
    ep1 = episode_factory(program_name="Same Prio Prog")
    ep2 = episode_factory(program_name="Same Prio Prog")
    # Both priority=0 (default), ep1 id < ep2 id

    _mk_approval_job(db_session, ep1)
    _mk_approval_job(db_session, ep2)

    groups, total = _group_approval_jobs(db_session)

    assert total == 2
    episodes = groups[0]["episodes"]
    assert len(episodes) == 2
    # FIFO: ep1 first (lower id)
    assert episodes[0]["id"] == ep1.id
    assert episodes[1]["id"] == ep2.id


# ---------------------------------------------------------------------------
# _fmt_duration_ms tests
# ---------------------------------------------------------------------------

def test_fmt_duration_ms_none():
    assert _fmt_duration_ms(None) == "?"


def test_fmt_duration_ms_under_one_hour():
    # 125 seconds = 2 minutes 5 seconds
    assert _fmt_duration_ms(125_000) == "2:05"


def test_fmt_duration_ms_zero():
    assert _fmt_duration_ms(0) == "0:00"


def test_fmt_duration_ms_over_one_hour():
    # 3723 seconds = 1 hour 2 minutes 3 seconds
    assert _fmt_duration_ms(3_723_000) == "1:02:03"


def test_fmt_duration_ms_exactly_one_minute():
    assert _fmt_duration_ms(60_000) == "1:00"


# ---------------------------------------------------------------------------
# _query_upgrade_candidates tests
# ---------------------------------------------------------------------------

def _mk_upgrade_candidate(
    db_session,
    ep,
    status: UpgradeStatus = UpgradeStatus.PENDING_REVIEW,
    owned_ms: int | None = 60_000,
    cand_ms: int | None = 62_000,
    url: str = "https://example.cz/candidate",
) -> UpgradeCandidate:
    c = UpgradeCandidate(
        episode_id=ep.id,
        candidate_url=url,
        owned_duration_ms=owned_ms,
        candidate_duration_ms=cand_ms,
        status=status,
    )
    db_session.add(c)
    db_session.flush()
    return c


def test_query_upgrade_candidates_empty(db_session):
    """No candidates → empty list."""
    assert _query_upgrade_candidates(db_session) == []


def test_query_upgrade_candidates_pending_review(db_session, episode_factory):
    """PENDING_REVIEW candidate appears in result."""
    ep = episode_factory(program_name="Upgrade Prog")
    _mk_upgrade_candidate(db_session, ep, status=UpgradeStatus.PENDING_REVIEW)

    result = _query_upgrade_candidates(db_session)

    assert len(result) == 1
    row = result[0]
    assert row["status"] == "pending_review"
    assert row["episode_title"] == ep.title


def test_query_upgrade_candidates_staged(db_session, episode_factory):
    """STAGED candidate appears in result."""
    ep = episode_factory()
    _mk_upgrade_candidate(db_session, ep, status=UpgradeStatus.STAGED)

    result = _query_upgrade_candidates(db_session)

    assert len(result) == 1
    assert result[0]["status"] == "staged"


def test_query_upgrade_candidates_resolved_excluded(db_session, episode_factory):
    """Terminal statuses (REPLACED, KEPT_OLD, DISMISSED) must not appear."""
    ep = episode_factory()
    for terminal in (UpgradeStatus.REPLACED, UpgradeStatus.KEPT_OLD, UpgradeStatus.DISMISSED):
        _mk_upgrade_candidate(
            db_session, ep,
            status=terminal,
            url=f"https://example.cz/{terminal.value}",
        )

    assert _query_upgrade_candidates(db_session) == []


def test_query_upgrade_candidates_diff_longer_candidate(db_session, episode_factory):
    """Candidate longer than owned → warn_ads=True, positive diff_str."""
    ep = episode_factory()
    _mk_upgrade_candidate(db_session, ep, owned_ms=60_000, cand_ms=62_000)

    result = _query_upgrade_candidates(db_session)

    assert result[0]["warn_ads"] is True
    assert result[0]["diff_str"].startswith("+")


def test_query_upgrade_candidates_diff_shorter_candidate(db_session, episode_factory):
    """Candidate shorter than owned → warn_ads=False, minus diff_str."""
    ep = episode_factory()
    _mk_upgrade_candidate(db_session, ep, owned_ms=62_000, cand_ms=60_000)

    result = _query_upgrade_candidates(db_session)

    assert result[0]["warn_ads"] is False
    assert result[0]["diff_str"].startswith("−")


def test_query_upgrade_candidates_unknown_durations(db_session, episode_factory):
    """Unknown durations → owned_fmt='?', cand_fmt='?', no diff."""
    ep = episode_factory()
    _mk_upgrade_candidate(db_session, ep, owned_ms=None, cand_ms=None)

    result = _query_upgrade_candidates(db_session)

    row = result[0]
    assert row["owned_fmt"] == "?"
    assert row["cand_fmt"] == "?"
    assert row["diff_str"] == ""
    assert row["warn_ads"] is False
