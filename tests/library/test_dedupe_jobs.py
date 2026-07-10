"""Tests for the dedupe_open_jobs helper (Bug B cleanup)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Base, Asset, AssetStatus, AssetType, DownloadJob, Episode, JobStatus,
    Program, Series, Station, Work,
)
from audiobiblio.library.pipelines.checks import dedupe_open_jobs


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _make_episode(session) -> Episode:
    """Minimal fixture: Station -> Program -> Series -> Work -> Episode."""
    station = Station(code="tst2", name="Test2")
    session.add(station)
    session.flush()
    program = Program(station_id=station.id, name="Prog2")
    session.add(program)
    session.flush()
    series = Series(program_id=program.id, name="S2")
    session.add(series)
    session.flush()
    work = Work(series_id=series.id, title="W2")
    session.add(work)
    session.flush()
    ep = Episode(work_id=work.id, title="Ep2")
    session.add(ep)
    session.flush()
    return ep


def test_dedupe_open_jobs_keeps_oldest_marks_rest_skipped(db_session):
    """Keep oldest (lowest ID) job, mark duplicates as SKIPPED."""
    ep = _make_episode(db_session)
    j1 = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.APPROVAL,
                      reason="first")
    j2 = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.PENDING,
                      reason="second")
    j3 = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.APPROVAL,
                      reason="third")
    db_session.add_all([j1, j2, j3])
    db_session.flush()

    removed = dedupe_open_jobs(db_session)
    db_session.refresh(j1)
    db_session.refresh(j2)
    db_session.refresh(j3)

    assert removed == 2
    assert j1.status == JobStatus.APPROVAL   # oldest kept unchanged
    assert j2.status == JobStatus.SKIPPED     # duplicate → SKIPPED
    assert j3.status == JobStatus.SKIPPED     # duplicate → SKIPPED
    assert "duplicate" in (j2.reason or "")
    assert "duplicate" in (j3.reason or "")


def test_dedupe_open_jobs_dry_run_no_changes(db_session):
    """dry_run=True reports count but does NOT modify DB."""
    ep = _make_episode(db_session)
    j1 = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.PENDING)
    j2 = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.PENDING)
    db_session.add_all([j1, j2])
    db_session.flush()

    removed = dedupe_open_jobs(db_session, dry_run=True)
    db_session.refresh(j1)
    db_session.refresh(j2)

    assert removed == 1
    assert j1.status == JobStatus.PENDING
    assert j2.status == JobStatus.PENDING


def test_dedupe_open_jobs_different_asset_types_no_collapse(db_session):
    """Jobs for different asset_types are independent — no dedup."""
    ep = _make_episode(db_session)
    j_audio = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.PENDING)
    j_meta = DownloadJob(episode_id=ep.id, asset_type=AssetType.META_JSON, status=JobStatus.PENDING)
    db_session.add_all([j_audio, j_meta])
    db_session.flush()

    removed = dedupe_open_jobs(db_session)
    assert removed == 0
    db_session.refresh(j_audio)
    db_session.refresh(j_meta)
    assert j_audio.status == JobStatus.PENDING
    assert j_meta.status == JobStatus.PENDING


def test_dedupe_open_jobs_ignores_closed_statuses(db_session):
    """SUCCESS/ERROR/SKIPPED jobs are closed — ignored by dedup."""
    ep = _make_episode(db_session)
    j_success = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                             status=JobStatus.SUCCESS)
    j_error = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                          status=JobStatus.ERROR)
    j_open = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                         status=JobStatus.PENDING)
    db_session.add_all([j_success, j_error, j_open])
    db_session.flush()

    removed = dedupe_open_jobs(db_session)
    assert removed == 0  # only one open job — nothing to dedup
