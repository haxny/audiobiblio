"""
Regression test: crawl_target must persist last_crawled_at/next_crawl_at even
when called with a detached ORM object (the manual crawl-now path via task_tracker).

Before the fix, writing timestamps to the detached `target` object was a no-op
on the active session, so the timestamps were never committed to the database.
"""
from __future__ import annotations
import pytest

from audiobiblio.core.db.models import CrawlTarget, CrawlTargetKind, ApprovalMode


def test_last_crawled_at_persisted_with_detached_target(db_session, monkeypatch):
    """
    Reproduce the detachment bug and verify the fix.

    The manual crawl-now path passes a CrawlTarget loaded in the HTTP request
    session to task_tracker.submit(); by the time the background task runs, that
    request session is dead and the ORM object is detached.  crawl_target() opens
    (or receives) a fresh session `s`, but the `target` argument is not attached
    to `s`, so writing `target.last_crawled_at = ...` followed by `s.commit()` was
    a silent no-op.

    We reproduce this in-process by:
      1. Creating a CrawlTarget in db_session.
      2. Expunging it — now it is detached from db_session.
      3. Passing the detached object + db_session to crawl_target().
      4. Asserting that last_crawled_at is non-null after the call.
    """
    # --- arrange ---
    target = CrawlTarget(
        url="https://example.cz/test-program",
        kind=CrawlTargetKind.PROGRAM,
        approval_mode=ApprovalMode.REVIEW,
        interval_hours=24,
    )
    db_session.add(target)
    db_session.commit()
    target_id = target.id

    # Detach the target from the session — simulates the request session being dead.
    db_session.expunge(target)
    assert target not in db_session, "target must be detached to reproduce the bug"

    # Patch probe_url so crawl_target takes the error path with no network I/O.
    import audiobiblio.acquire.crawler as crawler_mod

    def _fail_probe(url: str):
        raise RuntimeError("mock probe failure — no network I/O in tests")

    monkeypatch.setattr(crawler_mod, "probe_url", _fail_probe)

    # --- act ---
    result = crawler_mod.crawl_target(target, session=db_session)

    # --- assert ---
    assert result == 0  # error path always returns 0

    # Re-fetch from DB to verify the timestamps were actually committed.
    refreshed = db_session.get(CrawlTarget, target_id)
    assert refreshed.last_crawled_at is not None, (
        "last_crawled_at must be set in DB after crawl_target() — "
        "detached-object write was previously a silent no-op"
    )
    assert refreshed.next_crawl_at is not None, (
        "next_crawl_at must be set in DB after crawl_target()"
    )
