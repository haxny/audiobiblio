"""Per-crawl result recording (crawl_runs) — the raw material of the
health detectors. Recording must never break a crawl."""
from __future__ import annotations

from datetime import datetime

import pytest

from audiobiblio.core import crawl_runs
from audiobiblio.core.crawl_runs import crawl_note, record_crawl_result, recorded_crawl
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlRun, CrawlTarget, CrawlTargetKind, Episode, Program, Series,
    Station, Work,
)


def _target(db, url="https://dvojka.rozhlas.cz/hra-na-nedeli-7785729"):
    t = CrawlTarget(url=url, kind=CrawlTargetKind.PROGRAM, name="Hra", active=True,
                    interval_hours=24, approval_mode=ApprovalMode.AUTO)
    db.add(t)
    db.commit()
    return t


def _episode(db, n=1):
    st = db.query(Station).first() or Station(code="s", name="S")
    db.add(st)
    db.flush()
    p = db.query(Program).first() or Program(station_id=st.id, name="P")
    db.add(p)
    db.flush()
    se = db.query(Series).first() or Series(program_id=p.id, name="P")
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=f"W{n}")
    db.add(w)
    db.flush()
    ep = Episode(work_id=w.id, title=f"E{n}", ext_id=f"x{n}")
    db.add(ep)
    db.commit()
    return ep


def test_record_by_target_id(db_session):
    t = _target(db_session)
    record_crawl_result(db_session, target_id=t.id, path="crawl",
                        started_at=datetime(2026, 11, 7, 1, 0), items_seen=12,
                        new_episodes=2, jobs_queued=4)
    run = db_session.query(CrawlRun).one()
    assert (run.target_id, run.path, run.items_seen, run.new_episodes, run.jobs_queued) == (
        t.id, "crawl", 12, 2, 4)
    assert run.target_url == t.url
    assert run.error is None
    assert run.finished_at >= run.started_at


def test_record_by_url_resolves_target(db_session):
    t = _target(db_session)
    record_crawl_result(db_session, target_url=t.url + "/", path="show-sweep",
                        error="x" * 5000)
    run = db_session.query(CrawlRun).one()
    assert run.target_id == t.id
    assert len(run.error) <= 1000


def test_record_without_target_is_aggregate(db_session):
    record_crawl_result(db_session, path="serial-sweep", items_seen=40, new_episodes=3)
    run = db_session.query(CrawlRun).one()
    assert run.target_id is None and run.target_url is None


def test_record_never_raises(db_session):
    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("db gone")
        query = add = commit = get

        def rollback(self):
            pass

    assert record_crawl_result(Broken(), target_id=1, path="crawl") is None


def test_recorded_crawl_records_jobs_and_notes(db_session):
    t = _target(db_session)

    @recorded_crawl
    def crawl(target, session=None):
        crawl_note(path="station", items_seen=7)
        _episode(session, 1)
        _episode(session, 2)
        return 3

    assert crawl(t, session=db_session) == 3
    run = db_session.query(CrawlRun).one()
    assert (run.path, run.items_seen, run.jobs_queued, run.new_episodes) == ("station", 7, 3, 2)


def test_recorded_crawl_explicit_new_count_wins(db_session):
    t = _target(db_session)

    @recorded_crawl
    def crawl(target, session=None):
        _episode(session, 1)
        crawl_note(path="rapi", new_episodes=0)
        return 0

    crawl(t, session=db_session)
    assert db_session.query(CrawlRun).one().new_episodes == 0


def test_recorded_crawl_records_error_note(db_session):
    t = _target(db_session)

    @recorded_crawl
    def crawl(target, session=None):
        crawl_note(error="probe: yt-dlp probe timed out after 180s")
        return 0

    crawl(t, session=db_session)
    assert "timed out" in db_session.query(CrawlRun).one().error


def test_recorded_crawl_records_exception_and_reraises(db_session):
    t = _target(db_session)

    @recorded_crawl
    def crawl(target, session=None):
        raise ValueError("HTTP Error 404: Not Found")

    with pytest.raises(ValueError):
        crawl(t, session=db_session)
    run = db_session.query(CrawlRun).one()
    assert "404" in run.error


def test_crawl_note_outside_a_crawl_is_harmless():
    crawl_note(path="x", items_seen=1, error="e")  # no active recorder → no-op
    assert crawl_runs._current.get() is None
