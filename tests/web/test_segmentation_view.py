"""Smoke test for the /segmentation HTML view.

Strategy: mount the views router with a test DB that has one program;
GET /segmentation should return 200 with expected HTML markers.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import Base, Episode, Program, Series, Station, Work
from audiobiblio.web.deps import get_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def test_program(db_session):
    """A program WITH one episode (only such programs appear in the combobox)."""
    st = Station(code="CRo2", name="Dvojka")
    db_session.add(st)
    db_session.flush()
    prog = Program(station_id=st.id, name="Test View Program")
    db_session.add(prog)
    db_session.flush()
    ser = Series(program_id=prog.id, name="Test Series")
    db_session.add(ser)
    db_session.flush()
    work = Work(series_id=ser.id, title="Test Work")
    db_session.add(work)
    db_session.flush()
    db_session.add(Episode(work_id=work.id, title="Ep 1"))
    db_session.flush()
    return prog


@pytest.fixture()
def empty_program(db_session, test_program):
    """A program with NO episodes — must be excluded from the combobox."""
    prog = Program(station_id=test_program.station_id, name="Empty Program")
    db_session.add(prog)
    db_session.flush()
    return prog


@pytest.fixture()
def view_client(db_session):
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

class TestSegmentationView:
    def test_get_segmentation_returns_200(self, view_client, test_program):
        resp = view_client.get("/segmentation")
        assert resp.status_code == 200

    def test_response_contains_search_combobox(self, view_client, test_program):
        resp = view_client.get("/segmentation")
        assert b"program-search" in resp.content

    def test_response_contains_segmentace_text(self, view_client, test_program):
        resp = view_client.get("/segmentation")
        assert b"Segmentace" in resp.content

    def test_program_listed_as_name_with_station_code(self, view_client, test_program):
        """Naming convention: 'Pořad (kanál)' — e.g. 'Test View Program (CRo2)'."""
        resp = view_client.get("/segmentation")
        assert "Test View Program (CRo2)" in resp.text

    def test_program_without_episodes_excluded(self, view_client, empty_program):
        resp = view_client.get("/segmentation")
        assert "Empty Program" not in resp.text

    def test_episode_count_included(self, view_client, test_program):
        resp = view_client.get("/segmentation")
        assert '"episode_count": 1' in resp.text

    def test_route_registered_in_views_router(self):
        """Route census: /segmentation appears in views router."""
        from audiobiblio.web.views import router as views_router
        paths = [getattr(r, "path", None) for r in views_router.routes]
        assert "/segmentation" in paths
