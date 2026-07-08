"""Tests for the segmentation proposal and apply API endpoints.

TDD workflow: tests written first, router implemented to make them pass.

Test cases:
- Proposal shape: GET returns 200 with correct fields
- mode is "serialized" for 2 serialized episodes
- Apply dry=true: POST dry_run:true → {"actions": [...], "applied": false}
- Apply real: POST dry_run:false → {"applied": true}
- Titles filter: POST with titles=[non-matching] → empty actions
- 404 for unknown program_id on both GET and POST
- Route census: endpoints appear in the segmentation router
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
from audiobiblio.web.routers import segmentation


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
def client(db_session):
    app = FastAPI()
    app.include_router(segmentation.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def serialized_program(db_session):
    """A program with 2 serialized episodes (Author: Title N. díl pattern)."""
    st = Station(code="tst-seg", name="Test Seg Station")
    db_session.add(st)
    db_session.flush()

    prog = Program(station_id=st.id, name="Test Seg Program")
    db_session.add(prog)
    db_session.flush()

    series = Series(program_id=prog.id, name="Test Seg Series")
    db_session.add(series)
    db_session.flush()

    work = Work(series_id=series.id, title="Catch-all")
    db_session.add(work)
    db_session.flush()

    ep1 = Episode(
        work_id=work.id,
        title="Novak Jan: Kniha Prvni 1. díl",
        ext_id="seg-ep-1",
        url="https://example.cz/seg-1",
    )
    ep2 = Episode(
        work_id=work.id,
        title="Novak Jan: Kniha Prvni 2. díl",
        ext_id="seg-ep-2",
        url="https://example.cz/seg-2",
    )
    db_session.add(ep1)
    db_session.add(ep2)
    db_session.flush()

    return prog


# ---------------------------------------------------------------------------
# Route census
# ---------------------------------------------------------------------------

def test_proposal_route_registered():
    """Route census: GET /api/v1/segmentation/{program_id} in segmentation router."""
    paths = [getattr(r, "path", None) for r in segmentation.router.routes]
    assert "/api/v1/segmentation/{program_id}" in paths


def test_apply_route_registered():
    """Route census: POST /api/v1/segmentation/{program_id}/apply in segmentation router."""
    paths = [getattr(r, "path", None) for r in segmentation.router.routes]
    assert "/api/v1/segmentation/{program_id}/apply" in paths


# ---------------------------------------------------------------------------
# GET /api/v1/segmentation/{program_id}
# ---------------------------------------------------------------------------

class TestProposalEndpoint:
    def test_returns_404_for_unknown_program(self, client):
        resp = client.get("/api/v1/segmentation/99999")
        assert resp.status_code == 404

    def test_returns_200_with_correct_shape(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "proposed" in data
        assert "unassigned_count" in data
        assert "note" in data

    def test_mode_is_serialized_for_part_episodes(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        assert data["mode"] == "serialized"

    def test_proposed_list_has_expected_structure(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        assert len(data["proposed"]) >= 1
        pw = data["proposed"][0]
        assert "title" in pw
        assert "author" in pw
        assert "episode_count" in pw
        assert "episode_ids" in pw
        assert "signal" in pw
        assert "confidence" in pw

    def test_episode_count_matches_episode_ids_length(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        for pw in data["proposed"]:
            assert pw["episode_count"] == len(pw["episode_ids"])

    def test_both_episodes_in_cluster(self, client, serialized_program):
        """Serialized episodes with same author+title cluster into one ProposedWork."""
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        total_ep_ids = sum(pw["episode_count"] for pw in data["proposed"])
        assert total_ep_ids == 2  # both episodes clustered

    def test_unassigned_count_is_int(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        assert isinstance(data["unassigned_count"], int)
        assert data["unassigned_count"] == 0  # no generic episodes

    def test_note_is_non_empty_string(self, client, serialized_program):
        resp = client.get(f"/api/v1/segmentation/{serialized_program.id}")
        data = resp.json()
        assert isinstance(data["note"], str)
        assert len(data["note"]) > 0


# ---------------------------------------------------------------------------
# POST /api/v1/segmentation/{program_id}/apply
# ---------------------------------------------------------------------------

class TestApplyEndpoint:
    def test_returns_404_for_unknown_program(self, client):
        resp = client.post(
            "/api/v1/segmentation/99999/apply",
            json={"dry_run": True},
        )
        assert resp.status_code == 404

    def test_dry_run_true_returns_applied_false(self, client, serialized_program):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied"] is False
        assert isinstance(data["actions"], list)

    def test_dry_run_true_returns_non_empty_actions(self, client, serialized_program):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": True},
        )
        data = resp.json()
        assert len(data["actions"]) > 0

    def test_dry_run_false_returns_applied_true(self, client, serialized_program):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied"] is True
        assert isinstance(data["actions"], list)
        assert len(data["actions"]) > 0

    def test_titles_filter_non_matching_produces_no_create_actions(
        self, client, serialized_program
    ):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": True, "titles": ["__title_that_does_not_exist__"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        # No create/reparent actions for a non-matching title
        create_actions = [a for a in data["actions"] if "create:" in a or "reparent:" in a]
        assert create_actions == []

    def test_null_titles_applies_all_proposed(self, client, serialized_program):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": True, "titles": None},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["actions"]) > 0

    def test_response_has_actions_and_applied_keys(self, client, serialized_program):
        resp = client.post(
            f"/api/v1/segmentation/{serialized_program.id}/apply",
            json={"dry_run": True},
        )
        data = resp.json()
        assert set(data.keys()) >= {"actions", "applied"}
