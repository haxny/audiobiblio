"""Tests for the dedupe merge API endpoint (web/routers/dedupe.py)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import Base, Episode, Program, Series, Station, Work
from audiobiblio.web.deps import get_db
from audiobiblio.web.routers import dedupe


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
def client(db_session):
    app = FastAPI()
    app.include_router(dedupe.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


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


class TestMergeEndpointSelfMergeGuard:
    def test_returns_400_when_canonical_equals_duplicate(self, client, episode_factory):
        """POST /api/v1/dedupe/merge with canonical_id == duplicate_id → HTTP 400."""
        ep = episode_factory()

        resp = client.post(
            "/api/v1/dedupe/merge",
            json={"canonical_id": ep.id, "duplicate_id": ep.id, "dry_run": True},
        )

        assert resp.status_code == 400
        assert "must differ" in resp.json()["detail"]

    def test_returns_200_for_valid_dry_run(self, client, episode_factory):
        """POST /api/v1/dedupe/merge with distinct IDs and dry_run=True → HTTP 200."""
        canonical = episode_factory()
        dup = episode_factory()

        resp = client.post(
            "/api/v1/dedupe/merge",
            json={
                "canonical_id": canonical.id,
                "duplicate_id": dup.id,
                "dry_run": True,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert isinstance(data["actions"], list)
