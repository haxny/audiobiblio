"""Tests for the `dedupe-jobs` CLI command.

Uses typer.testing.CliRunner so the command runs in-process.
The test patches get_session() to inject an in-memory SQLite DB.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from audiobiblio.cli import app
from audiobiblio.core.db.models import (
    AssetType, Base, DownloadJob, Episode, JobStatus,
    Program, Series, Station, Work,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def episode_factory(db_session):
    counter = {"n": 0}

    def make(program_name: str = "Prog") -> Episode:
        counter["n"] += 1
        n = counter["n"]
        station = db_session.query(Station).filter_by(code="tst").one_or_none()
        if station is None:
            station = Station(code="tst", name="Test Station")
            db_session.add(station)
            db_session.flush()
        program = db_session.query(Program).filter_by(name=program_name).one_or_none()
        if program is None:
            program = Program(station_id=station.id, name=program_name)
            db_session.add(program)
            db_session.flush()
        series_name = f"{program_name} S"
        series = db_session.query(Series).filter_by(
            program_id=program.id, name=series_name).one_or_none()
        if series is None:
            series = Series(program_id=program.id, name=series_name)
            db_session.add(series)
            db_session.flush()
        work = Work(series_id=series.id, title=f"Work {n}")
        db_session.add(work)
        db_session.flush()
        ep = Episode(work_id=work.id, title=f"Episode {n}", ext_id=f"ext-{n}",
                     url=f"https://example.cz/ep-{n}")
        db_session.add(ep)
        db_session.flush()
        return ep

    return make


def _add_job(db_session, episode_id: int, asset_type: AssetType,
             status: JobStatus) -> DownloadJob:
    job = DownloadJob(episode_id=episode_id, asset_type=asset_type, status=status)
    db_session.add(job)
    db_session.flush()
    return job


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDedupeJobsCommand:
    def _run(self, db_session, *extra_args):
        with patch("audiobiblio.cli.get_session", return_value=db_session):
            return runner.invoke(app, ["dedupe-jobs", *extra_args])

    def test_no_duplicates_exits_zero(self, db_session, episode_factory):
        ep = episode_factory()
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)
        result = self._run(db_session)
        assert result.exit_code == 0, result.output

    def test_no_duplicates_reports_zero(self, db_session, episode_factory):
        ep = episode_factory()
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.PENDING)
        result = self._run(db_session)
        assert "0" in result.output or "no duplicate" in result.output.lower()

    def test_duplicate_open_jobs_kept_oldest_marked_rest_skipped(
        self, db_session, episode_factory
    ):
        """Two open APPROVAL jobs for same (episode, asset_type): oldest kept, newest → SKIPPED."""
        ep = episode_factory()
        old_job = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)
        new_job = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)

        result = self._run(db_session)
        assert result.exit_code == 0, result.output

        db_session.expire_all()
        assert old_job.status == JobStatus.APPROVAL  # oldest kept
        assert new_job.status == JobStatus.SKIPPED   # duplicate → SKIPPED

    def test_dry_run_does_not_modify(self, db_session, episode_factory):
        """--dry-run must report duplicates without writing any changes."""
        ep = episode_factory()
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)
        dup = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)

        result = self._run(db_session, "--dry-run")
        assert result.exit_code == 0, result.output

        db_session.expire_all()
        # No changes written — both still APPROVAL
        assert dup.status == JobStatus.APPROVAL

    def test_dry_run_reports_duplicate_count(self, db_session, episode_factory):
        ep = episode_factory()
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.PENDING)
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.PENDING)

        result = self._run(db_session, "--dry-run")
        assert "1" in result.output  # 1 duplicate to skip

    def test_closed_jobs_not_counted_as_duplicates(self, db_session, episode_factory):
        """ERROR/SKIPPED/SUCCESS jobs are closed; open + closed for same asset is NOT a duplicate."""
        ep = episode_factory()
        _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.ERROR)
        open_job = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)

        result = self._run(db_session)
        assert result.exit_code == 0, result.output

        db_session.expire_all()
        assert open_job.status == JobStatus.APPROVAL  # untouched

    def test_three_duplicates_keeps_only_oldest(self, db_session, episode_factory):
        ep = episode_factory()
        j1 = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)
        j2 = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.PENDING)
        j3 = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.RUNNING)

        result = self._run(db_session)
        assert result.exit_code == 0, result.output

        db_session.expire_all()
        assert j1.status == JobStatus.APPROVAL   # oldest preserved
        assert j2.status == JobStatus.SKIPPED
        assert j3.status == JobStatus.SKIPPED

    def test_different_asset_types_not_considered_duplicates(
        self, db_session, episode_factory
    ):
        ep = episode_factory()
        audio_job = _add_job(db_session, ep.id, AssetType.AUDIO, JobStatus.APPROVAL)
        meta_job = _add_job(db_session, ep.id, AssetType.META_JSON, JobStatus.APPROVAL)

        result = self._run(db_session)
        assert result.exit_code == 0, result.output

        db_session.expire_all()
        assert audio_job.status == JobStatus.APPROVAL
        assert meta_job.status == JobStatus.APPROVAL
