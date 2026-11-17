"""POST /api/v1/library/follow — report and apply files renamed/moved on disk."""
from __future__ import annotations

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, Program, Series, Station, Work,
)
from audiobiblio.library import pathfollow


def test_follow_dry_run_then_apply(client, db_session, silent_m4a_factory, tmp_path, monkeypatch):
    (tmp_path / "s" / "a").mkdir(parents=True)
    f = silent_m4a_factory(tmp_path / "s" / "a" / "01.m4a")
    st = Station(code="x", name="X")
    db_session.add(st)
    db_session.flush()
    p = Program(station_id=st.id, name="P")
    db_session.add(p)
    db_session.flush()
    se = Series(program_id=p.id, name="P")
    db_session.add(se)
    db_session.flush()
    w = Work(series_id=se.id, title="K")
    db_session.add(w)
    db_session.flush()
    ep = Episode(work_id=w.id, title="d1", episode_number=1)
    db_session.add(ep)
    db_session.flush()
    a = Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE, file_path=str(f))
    db_session.add(a)
    db_session.flush()
    pathfollow.fingerprint_asset(a)
    db_session.commit()
    (tmp_path / "s" / "a").rename(tmp_path / "s" / "b")
    monkeypatch.setattr(pathfollow, "DEFAULT_ROOTS", (str(tmp_path / "s"),))

    dry = client.post("/api/v1/library/follow", json={}).json()
    assert dry["applied"] is False and len(dry["moved"]) == 1
    assert a.file_path == str(f)

    done = client.post("/api/v1/library/follow", json={"apply": True}).json()
    assert done["applied"] is True and len(done["moved"]) == 1
    db_session.refresh(a)
    assert a.file_path == str(tmp_path / "s" / "b" / "01.m4a")
