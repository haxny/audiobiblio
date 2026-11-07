"""Read-only list of planned/recorded/failed live recordings (API + page)."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import LiveRecording, LiveStatus
from audiobiblio.web.deps import get_db

T0 = datetime(2026, 10, 10, 5, 59)


def _client(db_session):
    from audiobiblio.web.routers import live as live_router
    from audiobiblio.web.views import router as views_router
    app = FastAPI()
    app.include_router(live_router.router)
    app.include_router(views_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _rec(db, title, status, offset_h=0, **kw):
    r = LiveRecording(
        station_id="e26a", station_code="CRoDDur", kind="block", dedupe_key=title.lower(),
        title=title, series_title=title, schedule_start=T0 + timedelta(hours=offset_h),
        schedule_end=T0 + timedelta(hours=offset_h + 4), window_start=T0 + timedelta(hours=offset_h),
        window_end=T0 + timedelta(hours=offset_h + 4), stream_url="u", items=[], status=status,
        runs=[], **kw)
    db.add(r)
    db.commit()
    return r


def test_list_all_and_filter_by_status(db_session):
    _rec(db_session, "Blok I.", LiveStatus.COMPLETE, coverage=0.99, duration_s=14400.0)
    _rec(db_session, "Blok II.", LiveStatus.PLANNED, offset_h=24)
    _rec(db_session, "Blok III.", LiveStatus.FAILED, offset_h=48, error="missed")
    c = _client(db_session)
    body = c.get("/api/v1/live-recordings").json()
    assert body["total"] == 3
    assert [r["title"] for r in body["items"]] == ["Blok III.", "Blok II.", "Blok I."]
    assert body["counts"]["planned"] == 1 and body["counts"]["complete"] == 1
    planned = c.get("/api/v1/live-recordings", params={"status": "planned"}).json()
    assert [r["title"] for r in planned["items"]] == ["Blok II."]
    item = body["items"][-1]
    assert item["coverage"] == 0.99 and item["station_code"] == "CRoDDur"
    assert item["window_start"].startswith("2026-10-10T05:59")


def test_unknown_status_is_422(db_session):
    assert _client(db_session).get("/api/v1/live-recordings", params={"status": "bogus"}).status_code == 422


def test_page_renders(db_session):
    _rec(db_session, "Průlet nad symfoniemi Dmitrije Šostakoviče I.", LiveStatus.RECORDING)
    html = _client(db_session).get("/live-recordings").text
    assert "Průlet nad symfoniemi" in html and "recording" in html
