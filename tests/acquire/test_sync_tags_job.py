"""The nightly tag sync must commit in small batches (lock hygiene)."""
from __future__ import annotations

from types import SimpleNamespace

from audiobiblio.acquire import scheduler


class _Q:
    def __init__(self, ids):
        self.ids = ids

    def filter(self, *a):
        return self

    def all(self):
        return [(i,) for i in self.ids]


class _S:
    def __init__(self, n):
        self.n, self.commits, self.rollbacks = n, 0, 0

    def query(self, *a):
        return _Q(range(1, self.n + 1))

    def get(self, model, eid):
        return SimpleNamespace(id=eid)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_commits_in_batches(monkeypatch):
    sess = _S(120)
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: sess)
    monkeypatch.setattr("audiobiblio.library.sync.sync_episode_tags",
                        lambda s, ep, write: SimpleNamespace(diffs=[]))
    scheduler._sync_tags_job()
    # 120 episodes / 50 per batch → 2 intermediate commits + the final one
    assert sess.commits == 3


def test_failed_episode_is_rolled_back_and_sync_continues(monkeypatch):
    sess = _S(3)
    seen = []

    def flaky(s, ep, write):
        seen.append(ep.id)
        if ep.id == 2:
            raise RuntimeError("database is locked")
        return SimpleNamespace(diffs=[])

    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: sess)
    monkeypatch.setattr("audiobiblio.library.sync.sync_episode_tags", flaky)
    scheduler._sync_tags_job()
    assert seen == [1, 2, 3] and sess.rollbacks == 1
