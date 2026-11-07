"""Persisting planned recordings (look-ahead job) — dedupe, backups, re-plan."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from audiobiblio.acquire.live.detect import DDUR_ID, PLUS_ID, Candidate, merge_windows
from audiobiblio.acquire.live.planner import plan_recordings, run_lookahead
from audiobiblio.acquire.live.schedule import ScheduleItem
from audiobiblio.core.db.models import (
    Episode, LiveRecording, LiveStatus, Program, Series, Station, Work,
)

FIX = Path(__file__).parents[2] / "fixtures" / "live"
NOW = datetime(2026, 10, 9, 6, 0)  # naive UTC, before the Bruckner weekend


def _item(headline, since, till, station=DDUR_ID, title="x"):
    return ScheduleItem(id=f"{headline}-{since}", station_id=station,
                        since=datetime.fromisoformat(since), till=datetime.fromisoformat(till),
                        title=title, headline=headline, description="popis",
                        has_show=False, has_episode=False, has_audio=False)


def _block_windows():
    first = _item("Blok I.", "2026-10-10T08:00:00+02:00", "2026-10-10T12:00:00+02:00")
    rerun = _item("Blok I.", "2026-10-11T00:00:00+02:00", "2026-10-11T04:00:00+02:00")
    return merge_windows([Candidate("block", first, "blok i"), Candidate("block", rerun, "blok i")])


def test_plan_persists_primary_and_backup(db_session):
    stats = plan_recordings(db_session, _block_windows(), {DDUR_ID: "https://s/ddur_high.aac"}, now=NOW)
    rows = db_session.query(LiveRecording).order_by(LiveRecording.window_start).all()
    assert [r.status for r in rows] == [LiveStatus.PLANNED, LiveStatus.BACKUP]
    r = rows[0]
    assert r.station_code == "CRoDDur" and r.kind == "block"
    assert r.stream_url == "https://s/ddur_high.aac"
    assert r.window_start == datetime(2026, 10, 10, 5, 59)
    assert r.series_title == "Blok" and r.part == 1
    assert r.items[0]["headline"] == "Blok I."
    assert stats["new"] == 2


def test_replan_is_idempotent(db_session):
    plan_recordings(db_session, _block_windows(), {}, now=NOW)
    stats = plan_recordings(db_session, _block_windows(), {}, now=NOW)
    assert db_session.query(LiveRecording).count() == 2
    assert stats["new"] == 0


def test_replan_cancels_slot_that_left_the_schedule(db_session):
    plan_recordings(db_session, _block_windows(), {}, now=NOW)
    only_first = _block_windows()[:1]
    stats = plan_recordings(db_session, only_first, {}, now=NOW,
                            horizon=(datetime(2026, 10, 9), datetime(2026, 10, 16)))
    statuses = sorted(r.status for r in db_session.query(LiveRecording))
    assert statuses == sorted([LiveStatus.PLANNED, LiveStatus.CANCELLED])
    assert stats["cancelled"] == 1


def test_already_recorded_block_is_not_planned_again(db_session):
    plan_recordings(db_session, _block_windows()[:1], {}, now=NOW)
    row = db_session.query(LiveRecording).one()
    row.status = LiveStatus.COMPLETE
    db_session.commit()
    later = _item("Blok I.", "2026-10-17T00:00:00+02:00", "2026-10-17T04:00:00+02:00")
    stats = plan_recordings(db_session, merge_windows([Candidate("block", later, "blok i")]), {}, now=NOW)
    assert db_session.query(LiveRecording).count() == 1
    assert stats["skipped_done"] == 1


def test_default_stream_used_when_station_list_lacks_it(db_session):
    plan_recordings(db_session, _block_windows()[:1], {}, now=NOW)
    assert db_session.query(LiveRecording).one().stream_url == "https://rozhlas.stream/ddur_high.aac"


class _FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def fetch_stations(self):
        return json.loads((FIX / "stations.json").read_text())["data"]

    def fetch_schedule(self, start: date, days: int):
        self.calls.append((start, days))
        return self.items


def test_run_lookahead_end_to_end_with_db_known_serial(db_session):
    raw = json.loads((FIX / "schedule_flat_sample.json").read_text())
    items = [ScheduleItem.from_flat(d) for d in raw]
    # Urgent 5/6 is already in the library → must not be planned
    st = Station(code="CRoPlus", name="Plus")
    db_session.add(st); db_session.flush()
    p = Program(station_id=st.id, name="P"); db_session.add(p); db_session.flush()
    s = Series(program_id=p.id, name="S"); db_session.add(s); db_session.flush()
    w = Work(series_id=s.id, title="Urgent"); db_session.add(w); db_session.flush()
    db_session.add(Episode(work_id=w.id, title="Urgent 5/6", episode_number=5)); db_session.commit()

    client = _FakeClient(items)
    stats = run_lookahead(db_session, client=client, now=datetime(2026, 10, 9, 4, 0),
                          stations={DDUR_ID: ("block",), PLUS_ID: ("serial",)}, days=40)
    # from yesterday: a block seen yesterday keeps its rerun backup alive today
    assert client.calls == [(date(2026, 10, 8), 41)]
    titles = {r.title for r in db_session.query(LiveRecording)}
    assert any(t.startswith("Hudební a duchovní svět Antona Brucknera") for t in titles)
    assert "Urgent 5/6" not in titles and "Urgent 4/6" in titles
    assert stats["new"] >= 6


def test_past_windows_are_not_planned(db_session):
    stats = plan_recordings(db_session, _block_windows(), {}, now=datetime(2026, 10, 20))
    assert db_session.query(LiveRecording).count() == 0
    assert stats["past"] == 2


def _episode(db, program_name, work_title, ep_title, number):
    st = db.query(Station).filter_by(code="CRoPlus").one_or_none()
    if st is None:
        st = Station(code="CRoPlus", name="Plus")
        db.add(st); db.flush()
    p = Program(station_id=st.id, name=program_name); db.add(p); db.flush()
    s = Series(program_id=p.id, name=f"{program_name} S"); db.add(s); db.flush()
    w = Work(series_id=s.id, title=work_title); db.add(w); db.flush()
    db.add(Episode(work_id=w.id, title=ep_title, episode_number=number)); db.commit()


def test_available_elsewhere_matches_indexed_podcast_parts(db_session):
    from audiobiblio.acquire.live.planner import available_elsewhere
    # production shape: program 'Urgent', work 'Urgent. Poslouchejte…', episode '#2 Vyhlaseni…'
    _episode(db_session, "Urgent", "Urgent. Poslouchejte podcastovou sérii z urgentního příjmu",
             "#2 Vyhlaseni traumaplanu", 2)
    known = available_elsewhere(db_session)
    assert known("Urgent", 2)
    assert not known("Urgent", 3)
    assert not known("Slovenská lekce", 2)


def test_available_elsewhere_auto_target_covers_series(db_session):
    from audiobiblio.acquire.live.planner import available_elsewhere
    from audiobiblio.core.db.models import ApprovalMode, CrawlTarget, CrawlTargetKind
    db_session.add_all([
        CrawlTarget(url="https://vltava.rozhlas.cz/basneni-5989144", kind=CrawlTargetKind.PROGRAM,
                    name="BáSnění", approval_mode=ApprovalMode.AUTO, active=True),
        # a REVIEW archive target is no proof that new parts are on demand
        CrawlTarget(url="https://www.mujrozhlas.cz/slovo-o-hudbe", kind=CrawlTargetKind.PROGRAM,
                    name="Slovo o hudbě", approval_mode=ApprovalMode.REVIEW, active=True),
        CrawlTarget(url="https://x/inactive", kind=CrawlTargetKind.PROGRAM,
                    name="Znojmo", approval_mode=ApprovalMode.AUTO, active=False),
    ])
    db_session.commit()
    known = available_elsewhere(db_session)
    assert known("BáSnění - Anna Gažiová", 1)
    assert not known("Slovo o hudbě. Tajemství stavby varhan", 1)
    assert not known("Znojmo v královském hávu", 2)


def test_default_stations_are_ddur_vltava_plus():
    from audiobiblio.acquire.live.detect import VLTAVA_ID
    from audiobiblio.acquire.live.planner import stations_from_config
    from audiobiblio.core.config import Config
    st = stations_from_config(Config().live_stations)
    assert st == {DDUR_ID: ("block", "serial"), VLTAVA_ID: ("serial",), PLUS_ID: ("serial",)}


def test_program_name_persisted(db_session):
    plan_recordings(db_session, _block_windows()[:1], {}, now=NOW)
    assert db_session.query(LiveRecording).one().program_name == "Víkendové dopoledne"
