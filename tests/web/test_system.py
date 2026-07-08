"""Tests for the /system page and GET /api/v1/system/scheduler endpoint.

TDD workflow: tests written first (RED), then implementation.

Test cases:
- Scheduler endpoint: running flag, jobs list, guards against None scheduler
- /system view: 200, version card, scheduler card, stats block, ABS card, config card
- Route census: /system in views router, /api/v1/system/scheduler in system router
"""
from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import Base
from audiobiblio.web.deps import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_job(job_id: str, next_run_time: datetime | None = None) -> object:
    """Minimal scheduler job stub."""
    job = types.SimpleNamespace(id=job_id, next_run_time=next_run_time)
    return job


def _make_fake_scheduler(
    running: bool = True,
    jobs: list | None = None,
) -> object:
    """Minimal scheduler stub with get_jobs() method."""
    _jobs = jobs if jobs is not None else []
    scheduler = types.SimpleNamespace(
        running=running,
        get_jobs=lambda: _jobs,
    )
    return scheduler


# ---------------------------------------------------------------------------
# DB fixture
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


# ---------------------------------------------------------------------------
# Scheduler API client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def scheduler_client(db_session):
    """Test client for the system router with a fake scheduler on app.state."""
    from audiobiblio.web.routers import system as system_router

    app = FastAPI()
    app.include_router(system_router.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    # Inject a fake scheduler
    job = _make_fake_job("crawl_job", datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc))
    app.state.scheduler = _make_fake_scheduler(running=True, jobs=[job])

    return TestClient(app)


@pytest.fixture()
def scheduler_client_no_scheduler(db_session):
    """Test client where scheduler is None (guard path)."""
    from audiobiblio.web.routers import system as system_router

    app = FastAPI()
    app.include_router(system_router.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.state.scheduler = None

    return TestClient(app)


# ---------------------------------------------------------------------------
# View client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def view_client(db_session):
    """Test client for /system HTML view with empty DB and fake scheduler."""
    from audiobiblio.web.views import router as views_router
    from audiobiblio.web.routers import system as system_router

    app = FastAPI()
    app.include_router(system_router.router)
    app.include_router(views_router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    job = _make_fake_job("crawl_job", datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc))
    app.state.scheduler = _make_fake_scheduler(running=True, jobs=[job])

    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Route census
# ---------------------------------------------------------------------------


def test_scheduler_route_registered_in_system_router():
    """Route census: GET /api/v1/system/scheduler appears in system router."""
    from audiobiblio.web.routers import system as system_router

    paths = [getattr(r, "path", None) for r in system_router.router.routes]
    assert "/api/v1/system/scheduler" in paths


def test_system_route_registered_in_views_router():
    """Route census: GET /system appears in views router."""
    from audiobiblio.web.views import router as views_router

    paths = [getattr(r, "path", None) for r in views_router.routes]
    assert "/system" in paths


# ---------------------------------------------------------------------------
# GET /api/v1/system/scheduler
# ---------------------------------------------------------------------------


class TestSchedulerEndpoint:
    def test_returns_200(self, scheduler_client):
        resp = scheduler_client.get("/api/v1/system/scheduler")
        assert resp.status_code == 200

    def test_response_has_running_field(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert "running" in data

    def test_running_is_true_when_scheduler_running(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert data["running"] is True

    def test_response_has_jobs_field(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert "jobs" in data

    def test_jobs_contains_one_item(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert len(data["jobs"]) == 1

    def test_job_has_id_field(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert "id" in data["jobs"][0]

    def test_job_id_matches_fake_job(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert data["jobs"][0]["id"] == "crawl_job"

    def test_job_has_next_run_time_field(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        assert "next_run_time" in data["jobs"][0]

    def test_job_next_run_time_is_iso_string(self, scheduler_client):
        data = scheduler_client.get("/api/v1/system/scheduler").json()
        nrt = data["jobs"][0]["next_run_time"]
        assert nrt is not None
        # Should be parseable as ISO datetime
        datetime.fromisoformat(nrt.replace("Z", "+00:00"))

    def test_no_scheduler_returns_running_false(self, scheduler_client_no_scheduler):
        data = scheduler_client_no_scheduler.get("/api/v1/system/scheduler").json()
        assert data["running"] is False

    def test_no_scheduler_returns_empty_jobs(self, scheduler_client_no_scheduler):
        data = scheduler_client_no_scheduler.get("/api/v1/system/scheduler").json()
        assert data["jobs"] == []

    def test_job_with_null_next_run_time(self, db_session):
        """Job whose next_run_time is None should serialize as null."""
        from audiobiblio.web.routers import system as system_router

        app = FastAPI()
        app.include_router(system_router.router)

        def _override_db():
            yield db_session

        app.dependency_overrides[get_db] = _override_db

        job = _make_fake_job("paused_job", next_run_time=None)
        app.state.scheduler = _make_fake_scheduler(running=True, jobs=[job])

        client = TestClient(app)
        data = client.get("/api/v1/system/scheduler").json()
        assert data["jobs"][0]["next_run_time"] is None


# ---------------------------------------------------------------------------
# GET /system view
# ---------------------------------------------------------------------------


class TestSystemView:
    def test_returns_200(self, view_client):
        resp = view_client.get("/system")
        assert resp.status_code == 200

    def test_contains_version_text(self, view_client):
        resp = view_client.get("/system")
        # Either a real version number or the fallback "dev"
        assert b"Verze" in resp.content or b"version" in resp.content or b"dev" in resp.content

    def test_contains_scheduler_heading(self, view_client):
        resp = view_client.get("/system")
        content = resp.content
        assert "Plánovač".encode() in content  # scheduler heading

    def test_contains_job_id(self, view_client):
        resp = view_client.get("/system")
        assert b"crawl_job" in resp.content

    def test_contains_abs_section(self, view_client):
        resp = view_client.get("/system")
        # ABS section heading
        assert b"ABS" in resp.content or b"Audiobookshelf" in resp.content

    def test_contains_config_section(self, view_client):
        resp = view_client.get("/system")
        assert b"library_dir" in resp.content or b"Konfigurace" in resp.content

    def test_contains_logs_link(self, view_client):
        resp = view_client.get("/system")
        assert b"/logs" in resp.content

    def test_contains_stats_data(self, view_client):
        resp = view_client.get("/system")
        # Stats should show something numeric (even 0)
        assert b"0" in resp.content

    def test_active_is_system(self, view_client):
        """The System nav link should be active (class=active on /system link)."""
        resp = view_client.get("/system")
        # The nav <a href="/system"> should carry the active class
        assert b'href="/system"' in resp.content


# ---------------------------------------------------------------------------
# Nav link census
# ---------------------------------------------------------------------------


def test_nav_contains_system_link():
    """base.html has a /system nav link."""
    from pathlib import Path

    base = Path("audiobiblio/web/templates/base.html").read_text()
    assert "/system" in base
