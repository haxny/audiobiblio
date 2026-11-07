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


import pytest


@pytest.mark.parametrize("name", [
    "Četba na pokračování", "Četba s hvězdičkou", "Povídka", "Počteníčko",
    "Rozhlasová hra", "Osudy",
])
def test_literature_programs_with_capital_diacritics_selected(db_session, name):
    """SQLite lower() folds ASCII only: 'Četba' stayed 'Četba' and matched
    neither '%četb%' nor '%cetb%' — Četba na pokračování (Dvojka) was never
    swept and four live books went unnoticed (2026-11-07)."""
    w, ep = _mk_stub(db_session, program_name=name,
                     url="https://dvojka.rozhlas.cz/kniha-7654321")
    rows = serial_sweep._literature_stubs(db_session, days=60)
    assert ep.id in [r[0] for r in rows]


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


def test_show_sweep_ingests_live_episodes(db_session):
    """AUTO target + matching program: live show episodes become works with
    download jobs; known ext_ids and audio-less episodes are skipped."""
    import json
    from sqlalchemy import text
    w, _ = _mk_stub(db_session, program_name="Pokracovani za pet minut",
                    url="https://vltava.rozhlas.cz/pokracovani-za-pet-minut-7568224")
    db_session.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) "
        "VALUES ('https://vltava.rozhlas.cz/pokracovani-za-pet-minut-7568224', "
        "'PROGRAM', 'Pokracovani za pet minut', 1, 'AUTO', 24, datetime('now'))"))
    payload = {"data": [
        {"id": "aaaaaaaa-0000-0000-0000-000000000001",
         "attributes": {"title": "Jules Verne: Vynalez zkazy", "part": 1,
                        "audioLinks": [{"variant": "hls", "duration": 100,
                                        "url": "https://croaod.cz/s/vz1.m3u8"}]}},
        {"id": "aaaaaaaa-0000-0000-0000-000000000002",
         "attributes": {"title": "Bez audia", "part": 1, "audioLinks": []}},
    ]}

    class _Resp:
        code = 301
        headers = {"Location": "https://www.mujrozhlas.cz/rapi/view/show/0038fee4-b218-3a73-a4a4-04544f431aad"}

        def read(self):
            return json.dumps(payload).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()), \
         patch.object(serial_sweep.time, "sleep"):
        stats = serial_sweep.run_show_sweep(db_session)

    assert stats["new_episodes"] == 1
    ep = db_session.query(Episode).filter_by(
        ext_id="aaaaaaaa-0000-0000-0000-000000000001").first()
    assert ep is not None
    assert ep.discovery_source == "show-sweep"
    new_work = db_session.query(Work).get(ep.work_id)
    assert new_work.title == "Jules Verne: Vynalez zkazy"
    assert new_work.author == "Jules Verne"
    job = db_session.query(DownloadJob).filter_by(episode_id=ep.id).first()
    assert job is not None and job.status == JobStatus.PENDING


class TestTotalParts:
    """rAPI totalParts is the authoritative book length."""

    def _work(self, db, **kw):
        w, _ = _mk_stub(db)
        for k, v in kw.items():
            setattr(w, k, v)
        db.flush()
        return w

    def test_sets_total_from_rapi(self, db_session):
        w = self._work(db_session)
        assert serial_sweep.apply_total_parts(w, {"mirroredSerial": {"totalParts": 12}})
        assert (w.expected_total, w.expected_source) == (12, "rapi")

    def test_manual_total_wins(self, db_session):
        w = self._work(db_session, expected_total=10, expected_source="manual")
        assert not serial_sweep.apply_total_parts(w, {"mirroredSerial": {"totalParts": 12}})
        assert w.expected_total == 10

    def test_counted_total_is_corrected(self, db_session):
        w = self._work(db_session, expected_total=11, expected_source="user_offline")
        assert serial_sweep.apply_total_parts(w, {"mirroredSerial": {"totalParts": 12}})
        assert w.expected_total == 12

    def test_no_serial_info_changes_nothing(self, db_session):
        w = self._work(db_session)
        assert not serial_sweep.apply_total_parts(w, {})
        assert w.expected_total is None

    def test_known_episode_by_content_id_updates_its_work(self, db_session):
        w, ep = _mk_stub(db_session)
        ep.ext_id = "12131914"
        db_session.flush()
        e = {"id": "aeea36dc-0000-0000-0000-000000000000",
             "meta": {"ga": {"contentId": "12131914"}},
             "attributes": {"mirroredSerial": {"totalParts": 15}}}
        assert serial_sweep._total_for_known(db_session, e) == 1
        assert w.expected_total == 15


def test_show_sweep_groups_chapters_by_serial(db_session):
    """Chapters of one serial carry their own titles ('O Bruncvíkovi', 'O staré
    Praze') — they are parts of ONE work named by the serial (live: Staré
    pověsti české split into 15 one-part works waiting for 14 more parts)."""
    import json
    from sqlalchemy import text
    _mk_stub(db_session, program_name="Ctenarsky denik",
             url="https://vltava.rozhlas.cz/ctenarsky-denik-5012871")
    db_session.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES ('https://vltava.rozhlas.cz/ctenarsky-denik-5012871', "
        "'PROGRAM', 'Ctenarsky denik', 1, 'AUTO', 24, datetime('now'))"))
    serial = {"title": "Alois Jirásek: Staré pověsti české", "totalParts": 15}
    link = [{"variant": "hls", "duration": 100, "url": "https://croaod.cz/s/x.m3u8"}]
    payload = {"data": [
        {"id": f"bbbbbbbb-0000-0000-0000-00000000000{n}",
         "attributes": {"title": t, "part": n, "mirroredSerial": serial, "audioLinks": link}}
        for n, t in ((4, "O Bruncvíkovi"), (11, "O staré Praze"))]}

    class _Resp:
        code = 301
        headers = {"Location": "https://www.mujrozhlas.cz/rapi/view/show/0038fee4-b218-3a73-a4a4-04544f431aad"}

        def read(self):
            return json.dumps(payload).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db_session)
    eps = db_session.query(Episode).filter(Episode.discovery_source == "show-sweep").all()
    assert len({e.work_id for e in eps}) == 1
    w = db_session.get(Work, eps[0].work_id)
    assert w.title == "Alois Jirásek: Staré pověsti české" and w.expected_total == 15
    assert w.author == "Alois Jirásek"
    assert sorted((e.episode_number, e.title) for e in eps) == [(4, "O Bruncvíkovi"), (11, "O staré Praze")]


# --- show sweep: target → program (2026-11-07) ------------------------------
# Matching by exact lower(name) skipped 25 of 93 AUTO targets ("Cetba na
# pokracovani (CRo2) — stanice" vs "Četba na pokračování") and matched 19 to
# two or three twin programs, so live parts landed in a random twin.

def _prog(db, name, code, website, url=None):
    st = db.query(Station).filter_by(code=code).first()
    if not st:
        st = Station(code=code, name=code, website=website)
        db.add(st); db.flush()
    p = Program(station_id=st.id, name=name, url=url)
    db.add(p); db.flush()
    db.add(Series(program_id=p.id, name=name)); db.flush()
    return p


def _target(db, name, url):
    from sqlalchemy import text
    db.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES (:u, 'PROGRAM', :n, 1, 'AUTO', 24, datetime('now'))"),
        {"u": url, "n": name})


def _swept(db):
    return {url: pid for url, _name, pid in serial_sweep._sweep_targets(db)}


def test_sweep_target_matches_program_by_url_despite_name(db_session):
    url = "https://dvojka.rozhlas.cz/cetba-na-pokracovani-7230785"
    p = _prog(db_session, "Četba na pokračování", "CRo2", "https://dvojka.rozhlas.cz", url=url)
    _target(db_session, "Cetba na pokracovani (CRo2) — stanice", url)
    assert _swept(db_session)[url] == p.id


def test_sweep_target_matches_unaccented_name_on_the_right_station(db_session):
    url = "https://vltava.rozhlas.cz/povidky-klasiku-9584940"
    _prog(db_session, "Povídky klasiků", "CRoPlz", "https://plzen.rozhlas.cz")
    p = _prog(db_session, "Povídky klasiků", "CRo3", "https://vltava.rozhlas.cz")
    _target(db_session, "Povidky klasiku", url)
    assert _swept(db_session)[url] == p.id


def test_sweep_target_prefers_station_program_over_mujrozhlas_twin(db_session):
    url = "https://vltava.rozhlas.cz/povidka-5057586"
    _prog(db_session, "povidka", "mujrozhlas", "https://www.mujrozhlas.cz", url=url)
    p = _prog(db_session, "Povídka", "CRo3", "https://vltava.rozhlas.cz", url=url)
    _target(db_session, "povidka", url)
    rows = serial_sweep._sweep_targets(db_session)
    assert [r[2] for r in rows if r[0] == url] == [p.id]  # once, the station one


def test_sweep_target_matches_program_without_series(db_session):
    """Toulky ceskou minulosti: the program exists but was never crawled into."""
    url = "https://dvojka.rozhlas.cz/toulky-ceskou-minulosti-7230794"
    st = Station(code="mujrozhlas", name="m", website="https://www.mujrozhlas.cz")
    db_session.add(st); db_session.flush()
    p = Program(station_id=st.id, name="Toulky ceskou minulosti")
    db_session.add(p); db_session.flush()
    _target(db_session, "Toulky českou minulostí", url)
    assert _swept(db_session)[url] == p.id
