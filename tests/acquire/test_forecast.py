"""Download volume and queue forecast: how much did we take in, how much waits.

Live 2026-11-21: the back-catalog queue grew by ~8,000 jobs overnight and the
only way to judge the effect on disk was guessing. The forecast answers from
the DB: GB per day over the last days, average file size, queue size in GB.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from audiobiblio.acquire import forecast
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, Episode, JobStatus, Program, Series, Station, Work,
)

NOW = datetime(2026, 11, 21, 12, 0, 0)


def _job(db, status, size=None, finished=None, n=[0]):
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
    e = Episode(work_id=w.id, title="e", ext_id=f"f{n[0]}")
    db.add(e)
    db.flush()
    if size:
        db.add(Asset(episode_id=e.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                     file_path=f"/x/{n[0]}.m4a", size_bytes=size))
    db.add(DownloadJob(episode_id=e.id, asset_type=AssetType.AUDIO, status=status, finished_at=finished))
    db.commit()


def test_daily_volume_and_queue_estimate(db_session):
    gb = 10**9
    _job(db_session, JobStatus.SUCCESS, size=2 * gb, finished=NOW - timedelta(hours=3))
    _job(db_session, JobStatus.SUCCESS, size=4 * gb, finished=NOW - timedelta(days=1, hours=1))
    _job(db_session, JobStatus.SUCCESS, size=9 * gb, finished=NOW - timedelta(days=30))   # outside window
    for _ in range(10):
        _job(db_session, JobStatus.PENDING)
    f = forecast.download_forecast(db_session, now=NOW, days=7)
    assert f["window_days"] == 7
    assert f["downloaded_gb"] == 6.0 and f["files"] == 2
    assert f["gb_per_day"] == round(6.0 / 7, 2)
    assert f["avg_file_mb"] == 3000.0
    assert f["pending_jobs"] == 10 and f["pending_gb_estimate"] == 30.0


def test_no_history_gives_no_estimate(db_session):
    _job(db_session, JobStatus.PENDING)
    f = forecast.download_forecast(db_session, now=NOW)
    assert f["files"] == 0 and f["avg_file_mb"] is None and f["pending_gb_estimate"] is None
