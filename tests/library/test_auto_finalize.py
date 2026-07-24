"""The librarian: finished books shelve themselves (spec 2026-07-22)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, AvailabilityStatus, Episode, FieldOrigin,
    MetadataValue, Program, Series, Station, Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.library.pipelines import auto_finalize as af


NOW = datetime(2026, 7, 22)


@pytest.fixture()
def book(db_session, tmp_path, monkeypatch):
    monkeypatch.setitem(af.DESTINATIONS, "cetba na pokracovani",
                        (str(tmp_path / "fiction"), "book"))
    st = Station(code="CRo2", name="Dvojka"); db_session.add(st); db_session.flush()
    prog = Program(station_id=st.id, name="Četba na pokračování")
    db_session.add(prog); db_session.flush()
    ser = Series(program_id=prog.id, name="Četba na pokračování")
    db_session.add(ser); db_session.flush()
    w = Work(series_id=ser.id, title="Testkniha", author="Jan Autor", year=2020)
    db_session.add(w); db_session.flush()
    for n in (1, 2):
        ep = Episode(work_id=w.id, title=f"d{n}", episode_number=n,
                     published_at=NOW - timedelta(days=30))
        db_session.add(ep); db_session.flush()
        f = tmp_path / "lib" / f"{n:02d}.mp3"
        f.parent.mkdir(exist_ok=True); f.write_bytes(b"x")
        db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                             status=AssetStatus.COMPLETE, file_path=str(f)))
        record_value(db_session, "episode", ep.id, "narrator", "Petr Cteci",
                     FieldOrigin.SCRAPED, "t")
    db_session.flush()
    return w


def test_finished_book_is_shelved(db_session, tmp_path, book):
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any("SHELVE" in r for r in report), report
    dest = (tmp_path / "fiction" / "Jan Autor [audio]"
            / "Jan Autor - (2020) Testkniha (cte Petr Cteci, CRo2 2026)")
    # audio files take the user convention "{Autor} - ({rok}) {Titul} - NN"
    assert (dest / "Jan Autor - (2020) Testkniha - 01.mp3").exists()
    marker = db_session.query(MetadataValue).filter_by(
        entity_type="work", entity_id=book.id, field="final_path").one()
    assert str(dest) == marker.value


def test_idempotent_second_run(db_session, tmp_path, book):
    af.run_auto_finalize(db_session, now=NOW)
    report2 = af.run_auto_finalize(db_session, now=NOW)
    assert not any("SHELVE" in r for r in report2)


def test_fresh_book_waits_quiet_period(db_session, tmp_path, book):
    for e in book.episodes:
        e.published_at = NOW - timedelta(days=3)
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert report == []


def test_gone_part_blocks(db_session, tmp_path, book):
    book.episodes[0].availability_status = AvailabilityStatus.GONE
    db_session.flush()
    assert af.run_auto_finalize(db_session, now=NOW) == []


def test_missing_narrator_reports_waiting(db_session, tmp_path, book):
    db_session.query(MetadataValue).filter_by(field="narrator").delete()
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any("WAITING-METADATA" in r for r in report)
