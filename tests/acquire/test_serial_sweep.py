"""Tests for the serial-sweep literature safety net.

Network calls are stubbed; DB behavior uses the standard fixtures.
"""
from __future__ import annotations

from unittest.mock import patch

from audiobiblio.acquire import serial_sweep
from audiobiblio.core.db.models import (
    AssetType, DownloadJob, Episode, JobStatus, Program, Series, Station, Work,
)


def _mk_stub(db, program_name="Letní čtení",
             url="https://budejovice.rozhlas.cz/kniha-1234567"):
    st = db.query(Station).filter_by(code="sw").first()
    if not st:
        st = Station(code="sw", name="SW")
        db.add(st)
        db.flush()
    p = Program(station_id=st.id, name=program_name)
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name=program_name)
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title="Kniha X")
    db.add(w)
    db.flush()
    ep = Episode(work_id=w.id, title="Kniha X", url=url)
    db.add(ep)
    db.flush()
    return w, ep


def test_literature_stub_selected(db_session):
    w, ep = _mk_stub(db_session)
    rows = serial_sweep._literature_stubs(db_session, days=60)
    assert (ep.id, ep.url, w.id, w.title) in [tuple(r) for r in rows]


def test_non_literature_program_ignored(db_session):
    _mk_stub(db_session, program_name="Ranní zprávy")
    rows = serial_sweep._literature_stubs(db_session, days=60)
    assert rows == []


def test_expand_serial_creates_episodes_and_jobs(db_session):
    w, _ = _mk_stub(db_session)
    payload = {"data": [
        {"id": "11111111-1111-1111-1111-111111111111",
         "attributes": {"title": "Kniha X", "part": 1,
                        "audioLinks": [{"variant": "hls", "duration": 100,
                                        "url": "https://croaod.cz/s/x.m3u8"}]}},
        {"id": "22222222-2222-2222-2222-222222222222",
         "attributes": {"title": "Kniha X", "part": 2, "audioLinks": []}},
    ]}

    class _Resp:
        def read(self):
            import json
            return json.dumps(payload).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()):
        created = serial_sweep._expand_serial(db_session, "serial-uuid", w.id)

    assert created == 1  # part 2 has no audio → skipped
    eps = db_session.query(Episode).filter_by(work_id=w.id).all()
    nums = {e.episode_number for e in eps}
    assert 1 in nums
    job = (db_session.query(DownloadJob).join(Episode)
           .filter(Episode.work_id == w.id,
                   DownloadJob.status == JobStatus.PENDING).first())
    assert job is not None
    assert job.asset_type == AssetType.AUDIO


def test_expand_serial_idempotent(db_session):
    w, _ = _mk_stub(db_session)
    payload = {"data": [
        {"id": "33333333-3333-3333-3333-333333333333",
         "attributes": {"title": "Kniha X", "part": 1,
                        "audioLinks": [{"variant": "hls", "duration": 100,
                                        "url": "https://croaod.cz/s/y.m3u8"}]}},
    ]}

    class _Resp:
        def read(self):
            import json
            return json.dumps(payload).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()):
        assert serial_sweep._expand_serial(db_session, "s", w.id) == 1
        assert serial_sweep._expand_serial(db_session, "s", w.id) == 0
