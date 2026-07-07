"""
Tests for the overdue_count integration in views.index().

Strategy mirrors test_inbox_view.py: we test the pure helper
_compute_overdue_count(targets, now) rather than mounting the full FastAPI
router (which drags in config/scheduler dependencies).
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# helpers: build minimal CrawlTarget-like objects
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 7, 12, 0, 0)


def _tgt(
    *,
    active: bool = True,
    interval_hours: int = 24,
    next_crawl_at: datetime | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        active=active,
        interval_hours=interval_hours,
        next_crawl_at=next_crawl_at,
    )


# ---------------------------------------------------------------------------
# _compute_overdue_count
# ---------------------------------------------------------------------------

class TestComputeOverdueCount:
    def test_empty_list_returns_zero(self):
        from audiobiblio.web.views import _compute_overdue_count
        assert _compute_overdue_count([], _NOW) == 0

    def test_all_ok_returns_zero(self):
        from audiobiblio.web.views import _compute_overdue_count
        targets = [
            _tgt(next_crawl_at=_NOW + timedelta(hours=1)),
            _tgt(next_crawl_at=_NOW + timedelta(hours=5)),
        ]
        assert _compute_overdue_count(targets, _NOW) == 0

    def test_inactive_targets_not_counted(self):
        from audiobiblio.web.views import _compute_overdue_count
        targets = [
            _tgt(active=False, next_crawl_at=_NOW - timedelta(days=7)),
        ]
        assert _compute_overdue_count(targets, _NOW) == 0

    def test_due_targets_not_counted(self):
        """'due' means slightly overdue but within grace — not counted as overdue."""
        from audiobiblio.web.views import _compute_overdue_count
        targets = [
            _tgt(interval_hours=24, next_crawl_at=_NOW - timedelta(hours=6)),
        ]
        assert _compute_overdue_count(targets, _NOW) == 0

    def test_overdue_targets_counted(self):
        from audiobiblio.web.views import _compute_overdue_count
        targets = [
            _tgt(interval_hours=24, next_crawl_at=_NOW - timedelta(hours=13)),
            _tgt(interval_hours=24, next_crawl_at=_NOW - timedelta(hours=48)),
        ]
        assert _compute_overdue_count(targets, _NOW) == 2

    def test_mixed_states_counts_only_overdue(self):
        from audiobiblio.web.views import _compute_overdue_count
        targets = [
            _tgt(next_crawl_at=_NOW + timedelta(hours=1)),          # ok
            _tgt(active=False, next_crawl_at=_NOW - timedelta(days=7)),  # inactive
            _tgt(interval_hours=24, next_crawl_at=_NOW - timedelta(hours=6)),  # due (within grace)
            _tgt(interval_hours=24, next_crawl_at=_NOW - timedelta(hours=13)),  # overdue
        ]
        assert _compute_overdue_count(targets, _NOW) == 1

    def test_none_next_crawl_at_is_due_not_overdue(self):
        """A target that has never been crawled is 'due', not 'overdue'."""
        from audiobiblio.web.views import _compute_overdue_count
        targets = [_tgt(next_crawl_at=None)]
        assert _compute_overdue_count(targets, _NOW) == 0
