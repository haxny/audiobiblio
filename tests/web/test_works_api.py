"""Tests for PATCH /api/v1/works/{id} — expected_total management.
Also covers POST /api/v1/works/{id}/enrich — databazeknih enrichment trigger.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import (
    Base, Program, Series, Station, Work,
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
def client(db_session):
    from audiobiblio.web.routers import works
    app = FastAPI()
    app.include_router(works.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def work(db_session):
    st = Station(code="tw", name="Test")
    db_session.add(st)
    db_session.flush()
    p = Program(station_id=st.id, name="TW Prog")
    db_session.add(p)
    db_session.flush()
    s = Series(program_id=p.id, name="TW Series")
    db_session.add(s)
    db_session.flush()
    w = Work(series_id=s.id, title="Test Work")
    db_session.add(w)
    db_session.flush()
    return w


class TestPatchWorkExpectedTotal:
    def test_happy_path_returns_200(self, client, work):
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        assert resp.status_code == 200

    def test_response_contains_expected_total_and_source(self, client, work):
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        data = resp.json()
        assert data["expected_total"] == 5
        assert data["expected_source"] == "manual"

    def test_orm_column_updated(self, client, db_session, work):
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 7})
        db_session.expire(work)
        db_session.refresh(work)
        assert work.expected_total == 7
        assert work.expected_source == "manual"

    def test_provenance_row_recorded(self, client, db_session, work):
        from audiobiblio.core.db.models import FieldOrigin, MetadataValue

        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 3})
        mv = (
            db_session.query(MetadataValue)
            .filter_by(entity_type="work", entity_id=work.id, field="expected_total")
            .first()
        )
        assert mv is not None
        assert mv.origin == FieldOrigin.MANUAL
        assert mv.value == "3"
        assert mv.source == "user"

    def test_zero_rejected_422(self, client, work):
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 0})
        assert resp.status_code == 422

    def test_negative_rejected_422(self, client, work):
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": -1})
        assert resp.status_code == 422

    def test_not_found_404(self, client):
        resp = client.patch("/api/v1/works/99999", json={"expected_total": 5})
        assert resp.status_code == 404

    def test_update_replaces_previous_value(self, client, db_session, work):
        """Patching twice updates, not appends."""
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 10})
        db_session.expire(work)
        db_session.refresh(work)
        assert work.expected_total == 10


class TestEnrichWork:
    """POST /api/v1/works/{id}/enrich — fire-and-forget background enrichment."""

    def test_unknown_work_returns_404(self, client):
        resp = client.post("/api/v1/works/99999/enrich")
        assert resp.status_code == 404

    def test_known_work_returns_200_with_task_id(self, client, work, monkeypatch):
        """Endpoint returns 200 immediately with a task_id string."""
        # Prevent the background task from hitting the real DB/network
        from audiobiblio.web import tasks as _tasks
        submitted: list[str] = []

        def _fake_submit(name, fn, *args, **kwargs):
            submitted.append(name)
            return "fake-task-id"

        monkeypatch.setattr(_tasks.task_tracker, "submit", _fake_submit)

        resp = client.post(f"/api/v1/works/{work.id}/enrich")
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["task_id"] == "fake-task-id"
        assert any("enrich_work" in s for s in submitted)
