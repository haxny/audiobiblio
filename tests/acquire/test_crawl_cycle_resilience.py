"""One failing target must never end the whole crawl cycle.

2026-10-09: a 'database is locked' flush error left the session needing a
rollback; the error handler's commit then raised too and the cycle stopped
after 47 of 1,349 targets.
"""
from __future__ import annotations

from audiobiblio.acquire import crawler
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, CrawlTargetKind, Episode, Program, Series, Station, Work,
)


def _t(db, url):
    t = CrawlTarget(url=url, kind=CrawlTargetKind.PROGRAM, active=True,
                    interval_hours=24, approval_mode=ApprovalMode.REVIEW)
    db.add(t)
    db.commit()
    return t


def test_poisoned_session_does_not_stop_the_cycle(db_session, monkeypatch):
    st = Station(code="x", name="X"); db_session.add(st); db_session.flush()
    p = Program(station_id=st.id, name="P"); db_session.add(p); db_session.flush()
    se = Series(program_id=p.id, name="P"); db_session.add(se); db_session.flush()
    w = Work(series_id=se.id, title="W"); db_session.add(w); db_session.flush()
    db_session.add(Episode(work_id=w.id, title="a", ext_id="dup")); db_session.commit()
    bad, good = _t(db_session, "https://x/bad"), _t(db_session, "https://x/good")
    crawled = []

    def fake_crawl(t, session):
        if t.url.endswith("bad"):
            session.add(Episode(work_id=w.id, title="b", ext_id="dup"))
            session.flush()  # IntegrityError → session now needs rollback
        crawled.append(t.url)
        return 1

    monkeypatch.setattr(crawler, "get_session", lambda: db_session)
    monkeypatch.setattr(crawler, "crawl_target", fake_crawl)
    assert crawler.run_due_crawls() == 1
    assert crawled == ["https://x/good"]
    assert db_session.get(CrawlTarget, bad.id).next_crawl_at is not None
