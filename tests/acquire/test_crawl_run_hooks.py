"""Every crawl path leaves a crawl_runs row, and rAPI ingest stores
playableTill — the inputs of the daily health detectors."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import text

from audiobiblio.acquire import crawler, rapi_crawl, serial_sweep
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlRun, CrawlTarget, CrawlTargetKind, Episode,
)
from tests.acquire.test_rapi_crawl import SHOW, _existing, _rapi_ep, _run, _target
from tests.acquire.test_serial_sweep import _mk_stub

TILL = "2026-11-20T23:59:00+01:00"
TILL_UTC = datetime(2026, 11, 20, 22, 59)


# --- crawler.crawl_target ---------------------------------------------------

def test_rapi_path_records_run_with_new_count(db_session):
    t = _target(db_session)
    stats = rapi_crawl.RapiCrawlStats(show_uuid=SHOW, new=3, revived=1)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(rapi_crawl, "crawl_target_via_rapi", return_value=stats), \
         patch.object(crawler, "_weekly_archive_history"):
        crawler.crawl_target(t, session=db_session)
    run = db_session.query(CrawlRun).one()
    assert run.target_id == t.id
    assert run.path == "rapi"
    assert run.new_episodes == 4
    assert run.error is None


def test_probe_failure_is_recorded_as_error(db_session):
    t = _target(db_session, url="https://www.mujrozhlas.cz/kategorie-x",
                mode=ApprovalMode.AUTO)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(crawler, "_rapi_serials", return_value={}), \
         patch.object(crawler, "probe_url",
                      side_effect=RuntimeError("yt-dlp probe timed out after 180s")):
        assert crawler.crawl_target(t, session=db_session) == 0
    run = db_session.query(CrawlRun).one()
    assert run.path == "crawl"
    assert "timed out" in run.error
    assert run.new_episodes == 0


def test_station_crawl_records_items_seen(db_session):
    t = _target(db_session, url="https://olomouc.rozhlas.cz/poctenicko-6370902",
                mode=ApprovalMode.AUTO)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(crawler, "_rapi_serials", return_value={}), \
         patch.object(crawler, "probe_url", side_effect=RuntimeError("unsupported")), \
         patch.object(crawler, "fetch_station_page", return_value=("Počteníčko", "<html/>")), \
         patch.object(crawler, "fetch_archive_stubs", return_value=[]):
        crawler.crawl_target(t, session=db_session)
    run = db_session.query(CrawlRun).one()
    assert run.path == "station"
    assert run.items_seen == 0


# --- serial_sweep -------------------------------------------------------------

def _resp(payload, location="https://www.mujrozhlas.cz/rapi/view/show/0038fee4-b218-3a73-a4a4-04544f431aad"):
    class _Resp:
        code = 301
        headers = {"Location": location}

        def read(self):
            return json.dumps(payload).encode()
    return _Resp()


def _auto_target(db, url, name):
    db.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES (:u, 'PROGRAM', :n, 1, 'AUTO', 24, "
        "datetime('now'))"), {"u": url, "n": name})


def _live(eid, title, part=1, till=TILL):
    return {"id": eid, "attributes": {
        "title": title, "part": part,
        "audioLinks": [{"variant": "hls", "duration": 100, "playableTill": till,
                        "url": f"https://croaod.cz/s/{eid}.m3u8"}]}}


def test_show_sweep_records_run_per_target_and_playable_till(db_session):
    url = "https://vltava.rozhlas.cz/pokracovani-za-pet-minut-7568224"
    _mk_stub(db_session, program_name="Pokracovani za pet minut", url=url)
    _auto_target(db_session, url, "Pokracovani za pet minut")
    payload = {"data": [_live("aaaaaaaa-0000-0000-0000-000000000001", "Verne: Vynalez")]}
    with patch.object(serial_sweep, "_http", return_value=_resp(payload)), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db_session)
    run = db_session.query(CrawlRun).one()
    assert run.path == "show-sweep"
    assert run.target_id == db_session.query(CrawlTarget).filter_by(url=url).one().id
    assert run.new_episodes == 1 and run.error is None
    ep = db_session.query(Episode).filter_by(
        ext_id="aaaaaaaa-0000-0000-0000-000000000001").one()
    assert ep.playable_till == TILL_UTC


def test_show_sweep_records_unresolved_show_as_error(db_session):
    url = "https://vltava.rozhlas.cz/pokracovani-za-pet-minut-7568224"
    _mk_stub(db_session, program_name="Pokracovani za pet minut", url=url)
    _auto_target(db_session, url, "Pokracovani za pet minut")
    with patch.object(serial_sweep, "_http", return_value=_resp({}, location="")), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db_session)
    run = db_session.query(CrawlRun).one()
    assert run.error and "show" in run.error


def test_show_sweep_refreshes_playable_till_of_known_episode(db_session):
    url = "https://vltava.rozhlas.cz/pokracovani-za-pet-minut-7568224"
    w, _ = _mk_stub(db_session, program_name="Pokracovani za pet minut", url=url)
    _auto_target(db_session, url, "Pokracovani za pet minut")
    known = Episode(work_id=w.id, title="Dil 1", ext_id="bbbbbbbb-0000-0000-0000-000000000001")
    db_session.add(known)
    db_session.commit()
    payload = {"data": [_live("bbbbbbbb-0000-0000-0000-000000000001", "Dil 1")]}
    with patch.object(serial_sweep, "_http", return_value=_resp(payload)), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db_session)
    assert db_session.get(Episode, known.id).playable_till == TILL_UTC


def test_serial_sweep_records_one_aggregate_run(db_session):
    _mk_stub(db_session)  # one literature stub
    with patch.object(serial_sweep, "_http", return_value=_resp({}, location="")), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_serial_sweep(db_session)
    run = db_session.query(CrawlRun).one()
    assert run.path == "serial-sweep" and run.target_id is None
    assert run.items_seen == 1


def test_expand_serial_stores_playable_till(db_session):
    w, _ = _mk_stub(db_session)
    payload = {"data": [_live("cccccccc-0000-0000-0000-000000000001", "Kniha X")]}
    with patch.object(serial_sweep, "_http", return_value=_resp(payload)):
        serial_sweep._expand_serial(db_session, "s", w.id)
    ep = db_session.query(Episode).filter_by(
        ext_id="cccccccc-0000-0000-0000-000000000001").one()
    assert ep.playable_till == TILL_UTC


# --- rapi_crawl -----------------------------------------------------------------

def test_rapi_crawl_stores_playable_till_on_new_and_revived(db_session):
    from audiobiblio.core.db.models import AvailabilityStatus
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    gone = _existing(db_session, "https://api.mujrozhlas.cz/episodes/u9", ext_id="999",
                     title="Staré")
    gone.availability_status = AvailabilityStatus.GONE
    db_session.flush()
    new_e, revived_e = _rapi_ep("u-new", "222"), _rapi_ep("u9", "999")
    for e in (new_e, revived_e):
        e["attributes"]["audioLinks"][0]["playableTill"] = TILL
    _run(db_session, _target(db_session), [new_e, revived_e])
    assert db_session.query(Episode).filter_by(ext_id="222").one().playable_till == TILL_UTC
    assert db_session.get(Episode, gone.id).playable_till == TILL_UTC
