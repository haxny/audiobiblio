"""Space + intake control: see and switch the download pause flags from the web.

Live 2026-11: pausing downloads meant ssh + touching a file. The page/API shows
the state of both flags and the queue per tier, and flips them.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import AssetType, DownloadJob, Episode, JobStatus, Program, Series, Station, Work
from audiobiblio.web.deps import get_db
from audiobiblio.web.routers import system


@pytest.fixture()
def api(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_PAUSE_FILE", str(tmp_path / "PAUSE_DOWNLOADS"))
    monkeypatch.setenv("AUDIOBIBLIO_PAUSE_LOW_FILE", str(tmp_path / "PAUSE_LOW_PRIORITY"))
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _job(db, prio, status=JobStatus.PENDING, n=[0]):
    n[0] += 1
    st = db.query(Station).first() or Station(code="x", name="X")
    db.add(st)
    db.flush()
    p = Program(station_id=st.id, name=f"P{n[0]}")
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name=p.name)
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=f"W{n[0]}")
    db.add(w)
    db.flush()
    e = Episode(work_id=w.id, title="e", ext_id=f"x{n[0]}", priority=prio)
    db.add(e)
    db.flush()
    db.add(DownloadJob(episode_id=e.id, asset_type=AssetType.AUDIO, status=status))
    db.commit()


def test_status_reports_flags_and_pending_per_tier(api, db_session):
    _job(db_session, 10)
    _job(db_session, 10)
    _job(db_session, -10)
    _job(db_session, 10, status=JobStatus.SUCCESS)
    d = api.get("/api/v1/system/intake").json()
    assert d["paused"] is False and d["low_priority_paused"] is False
    assert d["pending"] == {"priority": 2, "back_catalog": 1}


def test_pause_and_resume_flip_the_flag_files(api, tmp_path):
    assert api.post("/api/v1/system/intake", json={"paused": True}).json()["paused"] is True
    assert (tmp_path / "PAUSE_DOWNLOADS").exists()
    assert api.post("/api/v1/system/intake", json={"paused": False}).json()["paused"] is False
    assert not (tmp_path / "PAUSE_DOWNLOADS").exists()


def test_back_catalog_switch_is_separate(api, tmp_path):
    d = api.post("/api/v1/system/intake", json={"low_priority_paused": True}).json()
    assert d["low_priority_paused"] is True and d["paused"] is False
    assert (tmp_path / "PAUSE_LOW_PRIORITY").exists()
    assert not (tmp_path / "PAUSE_DOWNLOADS").exists()
