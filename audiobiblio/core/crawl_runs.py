"""crawl_runs — record what each crawl of each target actually saw.

A crawl that 'succeeded' with 0 new items every day for months looked
exactly like a healthy one (2026-11-07: 25 AUTO targets never swept,
probe timeouts on the same ~15 targets daily). Every crawl path leaves one
CrawlRun row; the health detectors read them.

Hooks are deliberately tiny so they merge easily with crawler changes:

* ``@recorded_crawl`` on a ``crawl(target, session=None)`` function records
  one run per call (jobs = return value, error = raised exception);
* ``crawl_note(...)`` inside it adds what only the inner code knows
  (path taken, items seen, exact new count, a swallowed error);
* ``record_crawl_result(...)`` for loops that are not a single call
  (show sweep per target, serial sweep as one aggregate row).

Recording never raises: monitoring must not break crawling.
"""
from __future__ import annotations

import contextvars
import functools
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import func

from audiobiblio.core.db.models import CrawlRun, CrawlTarget, Episode
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

ERROR_MAX = 1000


@dataclass
class _Notes:
    path: str = "crawl"
    items_seen: int | None = None
    new_episodes: int | None = None
    error: str | None = None


_current: contextvars.ContextVar[_Notes | None] = contextvars.ContextVar(
    "crawl_run_notes", default=None)


def crawl_note(path: str | None = None, items_seen: int | None = None,
               new_episodes: int | None = None, error: str | None = None) -> None:
    """Annotate the crawl run in progress; a no-op outside ``@recorded_crawl``."""
    notes = _current.get()
    if notes is None:
        return
    if path is not None:
        notes.path = path
    if items_seen is not None:
        notes.items_seen = items_seen
    if new_episodes is not None:
        notes.new_episodes = new_episodes
    if error is not None:
        notes.error = error


def _resolve_target(session, target_id: int | None, target_url: str | None):
    if target_id is not None:
        t = session.get(CrawlTarget, target_id)
        return target_id, (target_url or (t.url if t is not None else None))
    if target_url:
        bare = target_url.rstrip("/")
        t = (session.query(CrawlTarget)
             .filter(CrawlTarget.url.in_([bare, bare + "/"])).first())
        return (t.id if t is not None else None), target_url
    return None, None


def record_crawl_result(session, *, path: str, target_id: int | None = None,
                        target_url: str | None = None,
                        started_at: datetime | None = None,
                        items_seen: int | None = None, new_episodes: int = 0,
                        jobs_queued: int = 0, error: str | None = None) -> CrawlRun | None:
    """Store one crawl result and commit. Returns the row, or None when
    recording failed (logged, never raised)."""
    try:
        tid, url = _resolve_target(session, target_id, target_url)
        now = utcnow()
        run = CrawlRun(
            target_id=tid, target_url=url, path=path,
            started_at=started_at or now, finished_at=now,
            items_seen=items_seen, new_episodes=int(new_episodes or 0),
            jobs_queued=int(jobs_queued or 0),
            error=(error.strip()[:ERROR_MAX] or None) if error else None,
        )
        session.add(run)
        session.commit()
        return run
    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        log.warning("crawl_run_record_failed", path=path, target_id=target_id,
                    url=target_url, error=str(e))
        return None


def _max_episode_id(session) -> int | None:
    try:
        return session.query(func.max(Episode.id)).scalar() or 0
    except Exception:
        return None


def recorded_crawl(fn):
    """Decorator for ``crawl(target, session=None) -> jobs queued``.

    New episodes default to the growth of max(episodes.id) during the call
    (crawls run one at a time in the scheduler thread; a concurrent sweep
    could inflate it — paths that know the exact number say so via
    crawl_note(new_episodes=...))."""
    @functools.wraps(fn)
    def wrapper(target, session=None, *args, **kwargs):
        if session is None:
            from audiobiblio.core.db.session import get_session
            session = get_session()
        tid, turl = target.id, target.url  # before a rollback can expire them
        notes = _Notes()
        token = _current.set(notes)
        started = utcnow()
        before = _max_episode_id(session)
        try:
            jobs = fn(target, session, *args, **kwargs)
        except Exception as e:
            _current.reset(token)
            try:
                session.rollback()  # the failure may have poisoned the session
            except Exception:
                pass
            record_crawl_result(session, path=notes.path, target_id=tid,
                                target_url=turl, started_at=started,
                                items_seen=notes.items_seen,
                                error=f"{type(e).__name__}: {e}")
            raise
        _current.reset(token)
        new = notes.new_episodes
        if new is None:
            after = _max_episode_id(session)
            new = max(0, after - before) if before is not None and after is not None else 0
        record_crawl_result(session, path=notes.path, target_id=tid,
                            target_url=turl, started_at=started,
                            items_seen=notes.items_seen, new_episodes=new,
                            jobs_queued=jobs or 0, error=notes.error)
        return jobs
    return wrapper
