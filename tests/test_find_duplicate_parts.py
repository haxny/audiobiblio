"""scripts/find_duplicate_parts.py — read-only report of duplicate part rows."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Base, Episode, Program, Series, Station, Work,
)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "find_duplicate_parts.py"


def _load():
    spec = importlib.util.spec_from_file_location("find_duplicate_parts", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "audiobiblio.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = Station(code="CRo3", name="Vltava")
    s.add(st)
    s.flush()
    p = Program(station_id=st.id, name="Povidka")
    s.add(p)
    s.flush()
    se = Series(program_id=p.id, name="Povidka")
    s.add(se)
    s.flush()
    w = Work(series_id=se.id, title="Dokumentarni povidky")
    s.add(w)
    s.flush()
    stream = "https://croaod.cz/stream/p3.m4a/master.m3u8"
    a = Episode(work_id=w.id, ext_id="26638c4a-35b2-3843-93ab-c0182c9a8d50",
                title="Miloš: Kluk", episode_number=3, url=stream,
                duration_ms=1_536_000, discovery_source="show-sweep")
    b = Episode(work_id=w.id, ext_id="12152327", title="Milos: Kluk",
                episode_number=3, url=stream, duration_ms=1_536_000)
    # part-number + duration twin with different stream URLs
    c = Episode(work_id=w.id, ext_id="0b1c2d3e-4f50-3617-8293-a4b5c6d7e8f9", title="P4", episode_number=4,
                url="https://croaod.cz/stream/p4a.m3u8", duration_ms=1_480_000)
    d = Episode(work_id=w.id, ext_id="12152328", title="P4", episode_number=4,
                url="https://croaod.cz/stream/p4b.m3u8", duration_ms=1_480_600)
    # a genuinely different part — never reported
    e = Episode(work_id=w.id, ext_id="12152329", title="P5", episode_number=5,
                url="https://croaod.cz/stream/p5.m3u8", duration_ms=1_700_000)
    s.add_all([a, b, c, d, e])
    s.flush()
    s.add(Asset(episode_id=a.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                file_path="/lib/Povidka/03.m4a"))
    s.add(Asset(episode_id=b.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                file_path="/lib/Povidka/03.m4a"))
    s.commit()
    ids = {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "e": e.id, "work": w.id}
    s.close()
    engine.dispose()
    return path, ids


def test_finds_same_stream_and_part_duration_pairs(db_path):
    path, ids = db_path
    pairs = _load().find_duplicate_pairs(path)
    got = {(p["keep_id"], p["dup_id"]) for p in pairs}
    assert (ids["a"], ids["b"]) in got  # rAPI row is the keeper
    assert len({frozenset(x) for x in got}) == 2
    assert all(ids["e"] not in pair for pair in got)
    pov = next(p for p in pairs if p["keep_id"] == ids["a"])
    assert "media_url" in pov["reasons"] and "same_file" in pov["reasons"]
    assert pov["work_id"] == ids["work"]


def test_report_lists_plain_work_urls(db_path, capsys):
    path, ids = db_path
    assert _load().main([str(path)]) == 0
    out = capsys.readouterr().out
    assert f"http://nasx:8321/works/{ids['work']}" in out


def test_database_is_opened_read_only(db_path):
    path, _ = db_path
    conn = _load().connect_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM episodes")
    conn.close()


def test_missing_database_is_an_error(tmp_path, capsys):
    assert _load().main([str(tmp_path / "nope.db")]) == 2
