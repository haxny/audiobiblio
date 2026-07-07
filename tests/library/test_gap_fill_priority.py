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
        work.expected_total = 0  # have=0, expected=0 → 0 >= 0 → not a gap
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
