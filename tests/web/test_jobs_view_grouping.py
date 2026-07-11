"""/jobs (Downloads) is episode-first: one row per episode, assets as
status badges of the LATEST job per asset type (user finding: the flat
asset-level log was unreadable — "člověk potřebuje vidět epizody").
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import (
    AssetType, Base, DownloadJob, Episode, JobStatus, Program, Series,
    Station, Work,
)
from audiobiblio.web.deps import get_db


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def episode_with_jobs(db_session):
    st = Station(code="CRo2", name="Dvojka")
    db_session.add(st)
    db_session.flush()
    prog = Program(station_id=st.id, name="Test Program")
    db_session.add(prog)
    db_session.flush()
    ser = Series(program_id=prog.id, name="Test Series")
    db_session.add(ser)
    db_session.flush()
    work = Work(series_id=ser.id, title="Testovací kniha")
    db_session.add(work)
    db_session.flush()
    ep = Episode(work_id=work.id, title="Testovací kniha, díl A", episode_number=4)
    db_session.add(ep)
    db_session.flush()
    # Two audio attempts — only the LATEST (success) counts on the page.
    db_session.add(DownloadJob(
        episode_id=ep.id, asset_type=AssetType.AUDIO,
        status=JobStatus.ERROR, error="stará chyba"))
    db_session.add(DownloadJob(
        episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.SUCCESS))
    db_session.add(DownloadJob(
        episode_id=ep.id, asset_type=AssetType.META_JSON, status=JobStatus.PENDING))
    db_session.flush()
    return ep


@pytest.fixture()
def view_client(db_session):
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=True)


class TestJobsGrouping:
    def test_one_row_per_episode(self, view_client, episode_with_jobs):
        resp = view_client.get("/jobs")
        assert resp.status_code == 200
        assert resp.text.count("Testovací kniha, díl A") == 1

    def test_part_number_badge_shown(self, view_client, episode_with_jobs):
        resp = view_client.get("/jobs")
        assert "díl 4" in resp.text

    def test_latest_audio_job_wins(self, view_client, episode_with_jobs):
        """The old ERROR audio attempt must not surface — SUCCESS is latest."""
        resp = view_client.get("/jobs")
        assert "stará chyba" not in resp.text
        assert 'title="audio: success"' in resp.text

    def test_status_filter_matches_latest_only(self, view_client, episode_with_jobs):
        assert "Testovací kniha, díl A" not in view_client.get("/jobs?status=error").text
        assert "Testovací kniha, díl A" in view_client.get("/jobs?status=pending").text

    def test_partial_route_grouped_too(self, view_client, episode_with_jobs):
        resp = view_client.get("/_partials/job_rows")
        assert resp.status_code == 200
        assert resp.text.count("Testovací kniha, díl A") == 1
