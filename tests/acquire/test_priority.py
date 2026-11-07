"""Download priority policy (owner decision 2026-11-07).

Rank 1: books/dramas expiring soon → 80–90.
Rank 2: serials / music cycles expiring soon → 50.
Ordinary book/drama/unclassified downloads → 10, music-other/news → 0.
Rank 3: serials / music cycles NOT expiring → -10 (after ordinary).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from audiobiblio.acquire import priority as P
from audiobiblio.core.db.models import (
    AssetType, CrawlTarget, CrawlTargetKind, DownloadJob, Episode, JobStatus,
)

NOW = datetime(2026, 11, 7, 12, 0)


def _till(days: float) -> datetime:
    return NOW + timedelta(days=days)


@pytest.mark.parametrize("cls,till,expected", [
    ("BOOK", None, 10),
    ("DRAMA", None, 10),
    ("BOOK", _till(60), 10),           # far expiry = ordinary
    ("BOOK", _till(20), 80),           # within 30 days
    ("BOOK", _till(3), 90),            # within 7 days = most urgent
    ("DRAMA", _till(29), 80),
    ("SERIAL", None, -10),
    ("MUSIC-CYCLE", None, -10),
    ("SERIAL", _till(365 * 5), -10),   # 2031-12-31 style "forever" link
    ("SERIAL", _till(10), 50),
    ("MUSIC-CYCLE", _till(2), 50),
    ("MUSIC-OTHER", None, 0),
    ("NEWS/TALK", _till(2), 0),        # index-only class never escalates
    (None, None, 10),                  # unclassified = legacy AUTO book target
    (None, _till(5), 90),              # unclassified AUTO = legacy book target
])
def test_policy_matrix(cls, till, expected):
    assert P.compute_priority(cls, till, now=NOW) == expected


def test_already_expired_link_is_not_escalated():
    assert P.compute_priority("BOOK", NOW - timedelta(days=1), now=NOW) == 10


def test_explicit_priority_base_overrides_class_base_but_not_expiry():
    assert P.compute_priority("SERIAL", None, priority_base=5, now=NOW) == 5
    assert P.compute_priority("SERIAL", _till(3), priority_base=5, now=NOW) == 50
    assert P.compute_priority("BOOK", _till(3), priority_base=95, now=NOW) == 95


def test_target_priority_reads_target_fields():
    t = CrawlTarget(url="u", kind=CrawlTargetKind.PROGRAM, content_class="SERIAL",
                    priority_base=None)
    assert P.target_priority(t, None, now=NOW) == -10
    assert P.target_priority(None, None, now=NOW) == 0


def test_parse_playable_till_takes_earliest_link_as_naive_utc():
    e = {"attributes": {"audioLinks": [
        {"playableTill": "2026-12-01T23:59:00+01:00"},
        {"playableTill": "2026-11-20T10:00:00+01:00"},
        {"url": "x"},
    ]}}
    assert P.parse_playable_till(e) == datetime(2026, 11, 20, 9, 0)


def test_parse_playable_till_absent_or_garbage():
    assert P.parse_playable_till({"attributes": {"audioLinks": [{"url": "x"}]}}) is None
    assert P.parse_playable_till({"attributes": {}}) is None
    assert P.parse_playable_till(
        {"attributes": {"audioLinks": [{"playableTill": "nope"}]}}) is None


def test_raise_priority_never_lowers():
    ep = Episode(title="x", priority=90)
    assert P.raise_priority(ep, -10) is False
    assert ep.priority == 90
    ep2 = Episode(title="y", priority=0)
    assert P.raise_priority(ep2, 50) is True
    assert ep2.priority == 50


def test_raise_priority_sets_negative_on_fresh_default():
    """A brand-new episode (priority None/0 default) takes the class's -10."""
    ep = Episode(title="x")
    P.apply_new_episode_priority(ep, -10)
    assert ep.priority == -10


def test_apply_new_episode_priority_keeps_higher():
    ep = Episode(title="x", priority=90)
    P.apply_new_episode_priority(ep, -10)
    assert ep.priority == 90


def test_gap_fill_does_not_undo_serial_demotion(db_session, episode_factory):
    from audiobiblio.library.pipelines.ingest import _apply_gap_fill_priority
    demoted, ordinary = episode_factory(), episode_factory()
    for ep in (demoted, ordinary):
        ep.work.expected_total = 5
    demoted.priority, ordinary.priority = -10, 0
    db_session.flush()
    _apply_gap_fill_priority(db_session, demoted, demoted.work)
    _apply_gap_fill_priority(db_session, ordinary, ordinary.work)
    assert (demoted.priority, ordinary.priority) == (-10, 10)


class TestBumpExpiring:
    def _ep(self, episode_factory, db, prio, till, status=JobStatus.PENDING):
        ep = episode_factory()
        ep.priority = prio
        ep.playable_till = till
        db.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=status))
        db.flush()
        return ep

    def test_bumps_pending_episodes_entering_window(self, db_session, episode_factory):
        book = self._ep(episode_factory, db_session, 10, _till(20))
        urgent = self._ep(episode_factory, db_session, 10, _till(3))
        serial = self._ep(episode_factory, db_session, -10, _till(10))
        far = self._ep(episode_factory, db_session, -10, _till(90))
        n = P.bump_expiring(db_session, now=NOW)
        assert n == 3
        assert (book.priority, urgent.priority, serial.priority, far.priority) == (80, 90, 50, -10)

    def test_never_lowers_and_ignores_non_pending(self, db_session, episode_factory):
        high = self._ep(episode_factory, db_session, 95, _till(3))
        approval = self._ep(episode_factory, db_session, 0, _till(3), JobStatus.APPROVAL)
        assert P.bump_expiring(db_session, now=NOW) == 0
        assert high.priority == 95 and approval.priority == 0

    def test_escalated_serial_stays_in_its_band(self, db_session, episode_factory):
        """A serial already at 50 must not jump into the book band at 7 days."""
        serial = self._ep(episode_factory, db_session, 50, _till(2))
        book = self._ep(episode_factory, db_session, 80, _till(2))
        assert P.bump_expiring(db_session, now=NOW) == 1
        assert (serial.priority, book.priority) == (50, 90)

    def test_idempotent(self, db_session, episode_factory):
        self._ep(episode_factory, db_session, 10, _till(20))
        assert P.bump_expiring(db_session, now=NOW) == 1
        assert P.bump_expiring(db_session, now=NOW) == 0
