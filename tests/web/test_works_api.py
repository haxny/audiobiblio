"""Tests for PATCH /api/v1/works/{id} — expected_total management.
Also covers POST /api/v1/works/{id}/enrich — databazeknih enrichment trigger,
and POST /api/v1/works/{id}/finalize — per-work folder finalization.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Base, Episode, Program, Series, Station, Work,
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

    def test_null_clears_expected_total(self, client, db_session, work):
        """PATCH with null clears the column and returns null in the response."""
        # First set a value
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        # Now clear it
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": None})
        assert resp.status_code == 200
        data = resp.json()
        assert data["expected_total"] is None
        assert data["expected_source"] is None

    def test_null_clears_orm_column(self, client, db_session, work):
        """PATCH null persists None to the DB column."""
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": None})
        db_session.expire(work)
        db_session.refresh(work)
        assert work.expected_total is None
        assert work.expected_source is None

    def test_null_records_manual_provenance_with_none(self, client, db_session, work):
        """PATCH null records a MANUAL MetadataValue row with value=None."""
        from audiobiblio.core.db.models import FieldOrigin, MetadataValue

        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 5})
        client.patch(f"/api/v1/works/{work.id}", json={"expected_total": None})
        mv = (
            db_session.query(MetadataValue)
            .filter_by(entity_type="work", entity_id=work.id, field="expected_total")
            .first()
        )
        assert mv is not None
        assert mv.origin == FieldOrigin.MANUAL
        assert mv.value is None

    def test_zero_still_rejected_422_after_null_feature(self, client, work):
        """0 is still invalid even with nullable field."""
        resp = client.patch(f"/api/v1/works/{work.id}", json={"expected_total": 0})
        assert resp.status_code == 422


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


class TestFinalizeWork:
    """POST /api/v1/works/{id}/finalize — explicit, previewed per-work folder move."""

    @pytest.fixture()
    def library_dir(self, tmp_path, monkeypatch) -> Path:
        d = tmp_path / "library"
        d.mkdir()
        from audiobiblio.web.routers import works as works_module
        monkeypatch.setattr(works_module, "default_library_root", lambda: d)
        return d

    @pytest.fixture()
    def complete_work(self, db_session, work, library_dir):
        """work fixture upgraded: expected_total=2, 2 episodes with COMPLETE audio files."""
        work.expected_total = 2
        work.expected_source = "manual"

        audio_dir = library_dir / "TW Prog (tw)"
        audio_dir.mkdir(parents=True, exist_ok=True)

        for i in range(1, 3):
            ep = Episode(
                work_id=work.id, title=f"Ep {i}", ext_id=f"fx-{i}",
                episode_number=i, url=f"https://example.cz/fx-{i}",
            )
            db_session.add(ep)
            db_session.flush()
            audio_file = audio_dir / f"Test Work - 0{i}.m4a"
            audio_file.write_bytes(b"audio")
            db_session.add(Asset(
                episode_id=ep.id, type=AssetType.AUDIO,
                status=AssetStatus.COMPLETE, file_path=str(audio_file),
            ))
            db_session.flush()
        return work

    def test_404_when_work_not_found(self, client, library_dir):
        resp = client.post("/api/v1/works/99999/finalize", json={"dry_run": True})
        assert resp.status_code == 404

    def test_409_when_expected_total_unset(self, client, work, library_dir):
        resp = client.post(f"/api/v1/works/{work.id}/finalize", json={"dry_run": True})
        assert resp.status_code == 409
        assert "expected_total" in resp.json()["detail"]

    def test_409_when_have_lt_expected(self, client, db_session, work, library_dir):
        work.expected_total = 5
        db_session.flush()
        resp = client.post(f"/api/v1/works/{work.id}/finalize", json={"dry_run": True})
        assert resp.status_code == 409
        assert "incomplete" in resp.json()["detail"].lower()

    def test_409_when_already_shelved(self, client, db_session, complete_work):
        """A work with a resolved final_path sits on the curated shelf —
        finalize must refuse (it would drag files out of the user's
        structure; happened live with Volynska rapsodie)."""
        from audiobiblio.core.db.models import FieldOrigin
        from audiobiblio.core.provenance import record_value
        record_value(db_session, "work", complete_work.id, "final_path",
                     "/media/nonfiction/biography [audio]/X", FieldOrigin.MANUAL,
                     "user_offline_adopt")
        db_session.flush()
        resp = client.post(
            f"/api/v1/works/{complete_work.id}/finalize", json={"dry_run": False}
        )
        assert resp.status_code == 409
        assert "shelved" in resp.json()["detail"]
        for a in db_session.query(Asset).filter(Asset.file_path.isnot(None)).all():
            assert Path(a.file_path).exists()

    def test_200_dry_run_returns_actions_and_applied_false(
        self, client, db_session, complete_work
    ):
        resp = client.post(
            f"/api/v1/works/{complete_work.id}/finalize", json={"dry_run": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["actions"], list)
        assert len(data["actions"]) > 0
        assert data["applied"] is False

    def test_200_default_is_dry_run(self, client, db_session, complete_work):
        resp = client.post(f"/api/v1/works/{complete_work.id}/finalize", json={})
        assert resp.status_code == 200
        assert resp.json()["applied"] is False

    def test_dry_run_does_not_move_files(self, client, db_session, complete_work):
        paths = [
            a.file_path
            for a in db_session.query(Asset).filter(Asset.file_path.isnot(None)).all()
        ]
        client.post(f"/api/v1/works/{complete_work.id}/finalize", json={"dry_run": True})
        for p in paths:
            assert Path(p).exists()

    def test_apply_moves_files_and_returns_applied_true(
        self, client, db_session, complete_work, library_dir
    ):
        old_paths = [
            a.file_path
            for a in db_session.query(Asset).filter(Asset.file_path.isnot(None)).all()
        ]
        resp = client.post(
            f"/api/v1/works/{complete_work.id}/finalize", json={"dry_run": False}
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        for p in old_paths:
            assert not Path(p).exists(), f"File must be moved away from {p}"
        # New per-work folder exists inside the program dir
        program_dir = library_dir / "TW Prog (tw)"
        work_dirs = [d for d in program_dir.iterdir() if d.is_dir()]
        assert len(work_dirs) == 1
