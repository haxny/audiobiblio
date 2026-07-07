"""Tests for the /gaps view helper _query_gaps.

Pattern: test the pure-ish helper function directly (avoids mounting the full
views router which drags in config/scheduler/template dependencies).
"""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType,
    Episode, Program, Series, Station, Work,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _setup(db, code: str) -> Series:
    st = db.query(Station).filter_by(code=code).first()
    if not st:
        st = Station(code=code, name=f"ST-{code}")
        db.add(st)
        db.flush()
    p = Program(station_id=st.id, name=f"P-{code}")
    db.add(p)
    db.flush()
    s = Series(program_id=p.id, name=f"S-{code}")
    db.add(s)
    db.flush()
    return s


_n = {"v": 0}


def _ep(db, work, *, num=None):
    _n["v"] += 1
    ep = Episode(work_id=work.id, title=f"Gv{_n['v']}", episode_number=num,
                 url=f"https://example.cz/gv-{_n['v']}")
    db.add(ep)
    db.flush()
    return ep


def _audio(db, ep):
    a = Asset(episode_id=ep.id, type=AssetType.AUDIO,
               status=AssetStatus.COMPLETE, file_path=f"/f/{ep.id}.m4a")
    db.add(a)
    db.flush()


# ---------------------------------------------------------------------------
# _query_gaps
# ---------------------------------------------------------------------------

class TestQueryGaps:
    def test_empty_when_no_incomplete_works(self, db_session):
        from audiobiblio.web.views import _query_gaps

        rows = _query_gaps(db_session)
        assert rows == []

    def test_includes_incomplete_work(self, db_session):
        from audiobiblio.web.views import _query_gaps

        series = _setup(db_session, "gv1")
        work = Work(series_id=series.id, title="GvWork1", expected_total=5)
        db_session.add(work)
        db_session.flush()
        ep = _ep(db_session, work, num=1)
        _audio(db_session, ep)

        rows = _query_gaps(db_session)
        assert len(rows) == 1
        row = rows[0]
        assert row["work_id"] == work.id
        assert row["have"] == 1
        assert row["expected"] == 5
        assert row["program_name"] == "P-gv1"

    def test_excludes_complete_work(self, db_session):
        from audiobiblio.web.views import _query_gaps

        series = _setup(db_session, "gv2")
        work = Work(series_id=series.id, title="GvWork2", expected_total=2)
        db_session.add(work)
        db_session.flush()
        for i in [1, 2]:
            ep = _ep(db_session, work, num=i)
            _audio(db_session, ep)

        rows = _query_gaps(db_session)
        assert rows == []

    def test_first_episode_id_in_row(self, db_session):
        from audiobiblio.web.views import _query_gaps

        series = _setup(db_session, "gv3")
        work = Work(series_id=series.id, title="GvWork3", expected_total=3)
        db_session.add(work)
        db_session.flush()
        ep1 = _ep(db_session, work, num=1)
        _audio(db_session, ep1)
        ep2 = _ep(db_session, work, num=2)

        rows = _query_gaps(db_session)
        assert rows[0]["first_episode_id"] == ep1.id

    def test_missing_numbers_in_row_when_dense(self, db_session):
        from audiobiblio.web.views import _query_gaps

        series = _setup(db_session, "gv4")
        work = Work(series_id=series.id, title="GvWork4", expected_total=4)
        db_session.add(work)
        db_session.flush()
        for i in [1, 3, 4]:
            ep = _ep(db_session, work, num=i)
            _audio(db_session, ep)

        rows = _query_gaps(db_session)
        assert rows[0]["missing_numbers"] == [2]
