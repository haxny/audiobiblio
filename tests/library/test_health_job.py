"""Daily health job: snapshot per detector per day, ONE digest only when
something is new or worse than the previous snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta

from audiobiblio.core.db.models import CrawlRun, HealthSnapshot
from audiobiblio.library.health import job
from audiobiblio.library.health.detectors import Detector
from audiobiblio.library.health.findings import Finding

DAY1 = datetime(2026, 11, 7, 6, 30)
DAY2 = DAY1 + timedelta(days=1)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, title, body):
        self.sent.append((title, body))
        return True


def _f(key, sev="warn", det="x", group=None):
    return Finding(detector=det, severity=sev, key=key, message=f"zpráva {key}",
                   link=f"http://nasx:8321/works/{key}", group=group)


def _detectors(state):
    """state: {detector id: list of findings | Exception}"""
    def make(did):
        def fn(session, now):
            v = state[did]
            if isinstance(v, Exception):
                raise v
            return v
        return fn
    return [Detector(did, f"Detektor {did}", make(did)) for did in state]


def test_first_run_stores_snapshots_and_sends_one_digest(db_session):
    n = FakeNotifier()
    state = {"a": [_f("1"), _f("2")], "b": []}
    res = job.run_health_check(db_session, now=DAY1, notifier=n, detectors=_detectors(state))
    snaps = {s.detector: s for s in db_session.query(HealthSnapshot).all()}
    assert snaps["a"].count == 2 and snaps["a"].severity == "warn"
    assert snaps["a"].keys == {"1": "warn", "2": "warn"}
    assert snaps["a"].details["items"][0]["link"] == "http://nasx:8321/works/1"
    assert snaps["b"].count == 0 and snaps["a"].day == "2026-11-07"
    assert len(n.sent) == 1
    title, body = n.sent[0]
    assert "audiobiblio" in title
    assert "Detektor a" in body and "http://nasx:8321/works/1" in body
    assert "Detektor b" not in body
    assert res["notified"] is True


def test_same_findings_next_day_send_nothing(db_session):
    n = FakeNotifier()
    state = {"a": [_f("1")]}
    job.run_health_check(db_session, now=DAY1, notifier=n, detectors=_detectors(state))
    job.run_health_check(db_session, now=DAY2, notifier=n, detectors=_detectors(state))
    assert len(n.sent) == 1


def test_new_item_next_day_is_reported_with_trend(db_session):
    n = FakeNotifier()
    job.run_health_check(db_session, now=DAY1, notifier=n,
                         detectors=_detectors({"a": [_f("1")]}))
    job.run_health_check(db_session, now=DAY2, notifier=n,
                         detectors=_detectors({"a": [_f("1"), _f("7")]}))
    assert len(n.sent) == 2
    body = n.sent[1][1]
    assert "2 (včera 1)" in body
    assert "works/7" in body and "works/1" not in body  # only what is new


def test_new_info_items_alone_do_not_notify(db_session):
    n = FakeNotifier()
    job.run_health_check(db_session, now=DAY1, notifier=n, detectors=_detectors({"a": []}))
    job.run_health_check(db_session, now=DAY2, notifier=n,
                         detectors=_detectors({"a": [_f("9", sev="info")]}))
    assert n.sent == []


def test_severity_escalation_notifies(db_session):
    n = FakeNotifier()
    job.run_health_check(db_session, now=DAY1, notifier=n,
                         detectors=_detectors({"a": [_f("1", sev="info")]}))
    job.run_health_check(db_session, now=DAY2, notifier=n,
                         detectors=_detectors({"a": [_f("1", sev="crit")]}))
    assert len(n.sent) == 1


def test_rerun_same_day_overwrites_and_does_not_resend(db_session):
    n = FakeNotifier()
    state = {"a": [_f("1")]}
    job.run_health_check(db_session, now=DAY1, notifier=n, detectors=_detectors(state))
    job.run_health_check(db_session, now=DAY1 + timedelta(hours=2), notifier=n,
                         detectors=_detectors(state))
    assert db_session.query(HealthSnapshot).count() == 1
    assert len(n.sent) == 1


def test_crashing_detector_is_stored_and_others_still_run(db_session):
    n = FakeNotifier()
    state = {"boom": RuntimeError("no such column"), "a": [_f("1")]}
    job.run_health_check(db_session, now=DAY1, notifier=n, detectors=_detectors(state))
    snaps = {s.detector: s for s in db_session.query(HealthSnapshot).all()}
    assert "no such column" in snaps["boom"].error
    assert snaps["a"].count == 1
    assert "selhal" in n.sent[0][1]


def test_groups_are_summarised(db_session):
    state = {"a": [_f("1", group="WAITING-METADATA"), _f("2", group="WAITING-METADATA"),
                   _f("3", group="AUTHOR-CHECK")]}
    job.run_health_check(db_session, now=DAY1, notifier=FakeNotifier(),
                         detectors=_detectors(state))
    s = db_session.query(HealthSnapshot).one()
    assert s.details["groups"] == {"WAITING-METADATA": 2, "AUTHOR-CHECK": 1}


def test_old_crawl_runs_are_pruned(db_session):
    db_session.add_all([
        CrawlRun(path="crawl", started_at=DAY1 - timedelta(days=200),
                 finished_at=DAY1 - timedelta(days=200), new_episodes=0, jobs_queued=0),
        CrawlRun(path="crawl", started_at=DAY1 - timedelta(days=5),
                 finished_at=DAY1 - timedelta(days=5), new_episodes=0, jobs_queued=0),
    ])
    db_session.commit()
    job.run_health_check(db_session, now=DAY1, notifier=FakeNotifier(),
                         detectors=_detectors({}))
    assert db_session.query(CrawlRun).count() == 1


def test_health_overview_trend_vs_seven_days(db_session):
    for i, cnt in enumerate([3, 4, 5, 6, 7, 8, 9, 10]):  # 8 days, oldest first
        day = (DAY1 - timedelta(days=7 - i)).strftime("%Y-%m-%d")
        db_session.add(HealthSnapshot(day=day, detector="expiring", count=cnt,
                                      severity="warn", keys={}, details={"items": []},
                                      notified=False))
    db_session.commit()
    rows = job.health_overview(db_session)
    row = next(r for r in rows if r["id"] == "expiring")
    assert row["count"] == 10
    assert row["trend"] == 7          # vs the snapshot 7 days earlier (count 3)
    assert row["label"]
    assert any(r["id"] == "disk" and r["count"] is None for r in rows)  # never run
