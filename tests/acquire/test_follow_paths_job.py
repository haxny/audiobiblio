"""The nightly follow-the-files job must survive a database that is locked by
another writer (live 2026-11-18: it died at 02:40 and nothing was written)."""
from __future__ import annotations

from sqlalchemy.exc import OperationalError

from audiobiblio.acquire import scheduler
from audiobiblio.library import pathfollow
from audiobiblio.library.pathfollow import FollowReport


class _Sess:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def _wire(monkeypatch, outcomes):
    sess, calls, waits = _Sess(), [], []
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: sess)
    monkeypatch.setattr(pathfollow, "backfill_fingerprints", lambda s, limit: 7)

    def fake_follow(s, roots, apply):
        calls.append(apply)
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(pathfollow, "follow_paths", fake_follow)
    monkeypatch.setattr(scheduler, "_follow_sleep", waits.append)
    return sess, calls, waits


def _locked():
    return OperationalError("UPDATE", {}, Exception("database is locked"))


def test_job_retries_after_a_lock_and_finishes(monkeypatch):
    sess, calls, waits = _wire(monkeypatch, [_locked(), FollowReport()])
    scheduler._follow_paths_job()
    assert calls == [True, True] and sess.rollbacks == 1
    assert waits == [scheduler.FOLLOW_RETRY_WAIT_S]


def test_job_gives_up_after_three_locks_without_raising(monkeypatch):
    sess, calls, waits = _wire(monkeypatch, [_locked(), _locked(), _locked()])
    scheduler._follow_paths_job()          # logged as follow_paths_error, no exception
    assert len(calls) == scheduler.FOLLOW_ATTEMPTS


def test_other_errors_are_not_retried(monkeypatch):
    sess, calls, waits = _wire(monkeypatch, [RuntimeError("boom")])
    scheduler._follow_paths_job()
    assert len(calls) == 1 and waits == []
