"""Tests for gap-fill priority injection.

Two surfaces:
  1. ingest.upsert_from_item → episode.priority = 10 when work has a gap
  2. checks.plan_downloads → job.reason contains "gap-fill" when work has a gap
"""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType,
    Episode, DownloadJob, JobStatus, Work,
)


# ---------------------------------------------------------------------------
# plan_downloads reason
# ---------------------------------------------------------------------------

class TestGapFillJobReason:
    def test_gap_fill_reason_when_work_has_gap(self, db_session, episode_factory):
        """plan_downloads appends 'gap-fill' to reason when work has expected_total > have."""
        from audiobiblio.library.pipelines.checks import plan_downloads

        ep = episode_factory()
        work = db_session.get(Work, ep.work_id)
        work.expected_total = 5  # have=0, expected=5 → gap
        db_session.flush()

        jobs = plan_downloads(db_session, ep.id)
        assert jobs, "expected at least one job"
        assert all("gap-fill" in (j.reason or "") for j in jobs)

    def test_no_gap_fill_reason_when_no_expected_total(self, db_session, episode_factory):
        """No 'gap-fill' tag when work.expected_total is None."""
        from audiobiblio.library.pipelines.checks import plan_downloads

        ep = episode_factory()

        jobs = plan_downloads(db_session, ep.id)
        assert jobs
        assert all("gap-fill" not in (j.reason or "") for j in jobs)

    def test_no_gap_fill_reason_when_already_complete(self, db_session, episode_factory):
        """No 'gap-fill' tag when have >= expected_total."""
        from audiobiblio.library.pipelines.checks import plan_downloads

        ep = episode_factory()
        work = db_session.get(Work, ep.work_id)
        # Add a COMPLETE AUDIO asset so have=1
        db_session.add(
            Asset(episode_id=ep.id, type=AssetType.AUDIO,
                  status=AssetStatus.COMPLETE, file_path="/fake/complete.m4a")
        )
        work.expected_total = 1  # have=1, expected=1 → 1 >= 1 → not a gap
        db_session.flush()

        jobs = plan_downloads(db_session, ep.id)
        assert all("gap-fill" not in (j.reason or "") for j in jobs)


# ---------------------------------------------------------------------------
# upsert_from_item priority
# ---------------------------------------------------------------------------

class TestGapFillIngestPriority:
    def test_new_episode_gets_priority_10_when_gap(self, db_session):
        """upsert_from_item sets priority=10 on new episode when work has a gap."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        # Create first episode via ingest, give it complete audio
        ep1, work = upsert_from_item(
            db_session,
            url="https://example.cz/gfprio/1",
            item_title="Ep 1",
            series_name="GFSeries",
            author=None,
            uploader=None,
            program_name="GFProg_prio",
            episode_number=1,
        )
        db_session.add(
            Asset(episode_id=ep1.id, type=AssetType.AUDIO,
                  status=AssetStatus.COMPLETE, file_path="/fake/gf1.m4a")
        )
        # Set expected_total → gap exists (have=1, expected=5)
        work.expected_total = 5
        db_session.commit()

        # Ingest new episode → should get priority=10
        ep2, _ = upsert_from_item(
            db_session,
            url="https://example.cz/gfprio/2",
            item_title="Ep 2",
            series_name="GFSeries",
            author=None,
            uploader=None,
            program_name="GFProg_prio",
            episode_number=2,
        )
        db_session.expire(ep2)
        db_session.refresh(ep2)
        assert ep2.priority == 10

    def test_no_priority_boost_when_no_expected_total(self, db_session):
        """No priority boost when work.expected_total is None."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        ep, _ = upsert_from_item(
            db_session,
            url="https://example.cz/noboost/1",
            item_title="Ep 1",
            series_name="NBS",
            author=None,
            uploader=None,
            program_name="NoBooostProg",
            episode_number=1,
        )
        db_session.expire(ep)
        db_session.refresh(ep)
        assert ep.priority == 0

    def test_no_priority_boost_when_work_complete(self, db_session):
        """No priority boost when have >= expected_total."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        # Ingest ep1 + audio → have=1
        ep1, work = upsert_from_item(
            db_session,
            url="https://example.cz/fullwork/1",
            item_title="Ep 1",
            series_name="FWSeries",
            author=None,
            uploader=None,
            program_name="FullWorkProg",
            episode_number=1,
        )
        db_session.add(
            Asset(episode_id=ep1.id, type=AssetType.AUDIO,
                  status=AssetStatus.COMPLETE, file_path="/fake/fw1.m4a")
        )
        # expected_total = 1 → work is already complete
        work.expected_total = 1
        db_session.commit()

        ep2, _ = upsert_from_item(
            db_session,
            url="https://example.cz/fullwork/2",
            item_title="Ep 2",
            series_name="FWSeries",
            author=None,
            uploader=None,
            program_name="FullWorkProg",
            episode_number=2,
        )
        db_session.expire(ep2)
        db_session.refresh(ep2)
        assert ep2.priority == 0

    def test_episode_number_beyond_expected_ignored(self, db_session, episode_factory):
        """Episode numbers beyond expected_total don't crash completeness computation."""
        from audiobiblio.library.pipelines.completeness import work_completeness
        from audiobiblio.core.db.models import Episode as EpisodeModel

        # Create first episode with number 1 to establish numbering (1/5 = 20% so far)
        ep1 = episode_factory()
        work = db_session.get(Work, ep1.work_id)
        ep1.episode_number = 1
        work.expected_total = 5
        db_session.flush()

        # Add 4 more episodes with numbers 2-5 to reach 80% threshold (5 out of 5)
        for i in range(2, 6):
            ep = EpisodeModel(
                work_id=work.id,
                episode_number=i,
                title=f"Ep {i}",
                url=f"https://example.cz/ep{i}",
            )
            db_session.add(ep)
        db_session.flush()

        # Add episode with number 99 and COMPLETE AUDIO
        ep_outlier = EpisodeModel(
            work_id=work.id,
            episode_number=99,
            title="Bonus",
            url="https://example.cz/bonus",
        )
        db_session.add(ep_outlier)
        db_session.flush()
        db_session.add(
            Asset(episode_id=ep_outlier.id, type=AssetType.AUDIO,
                  status=AssetStatus.COMPLETE, file_path="/fake/bonus.m4a")
        )
        db_session.flush()

        # Compute completeness — should not crash, should not include 99 in missing_numbers
        comp = work_completeness(db_session, work)
        assert comp.have == 1
        assert comp.expected == 5
        # 99 > expected_total so it's ignored in missing_numbers computation
        assert comp.missing_numbers == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# _group_approval_jobs gap_fill flag
# ---------------------------------------------------------------------------

class TestGroupApprovalJobsGapFill:
    def test_gap_fill_flag_when_reason_contains_gap_fill(self, db_session, episode_factory):
        """_group_approval_jobs sets episode['gap_fill'] = True when job.reason contains 'gap-fill'."""
        from audiobiblio.web.views import _group_approval_jobs

        ep = episode_factory()
        work = db_session.get(Work, ep.work_id)
        work.expected_total = 5
        db_session.flush()

        # Create a job with 'gap-fill' in reason and APPROVAL status
        job = DownloadJob(
            episode_id=ep.id,
            asset_type=AssetType.AUDIO,
            status=JobStatus.APPROVAL,
            reason="asset:AUDIO status MISSING; gap-fill"
        )
        db_session.add(job)
        db_session.commit()

        groups, total = _group_approval_jobs(db_session)
        assert total == 1
        assert len(groups) == 1
        episodes = groups[0]["episodes"]
        assert len(episodes) == 1
        assert episodes[0]["gap_fill"] is True
