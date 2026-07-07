"""
Unit tests for target_state() — pure helper that classifies a CrawlTarget
freshness without touching the DB or wall clock.

State transitions:
  "inactive" — target.active is False (checked first, regardless of timestamps)
  "overdue"  — next_crawl_at < now − 0.5 × interval_hours  (missed by more than half an interval)
  "due"      — next_crawl_at <= now  (including next_crawl_at is None)
  "ok"       — otherwise (next_crawl_at is in the future)
"""
from __future__ import annotations

import types
import pytest
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_target(
    *,
    active: bool = True,
    interval_hours: int = 24,
    next_crawl_at: datetime | None = None,
) -> types.SimpleNamespace:
    """Construct a duck-typed stand-in for CrawlTarget (no DB session required).

    target_state() only reads .active, .interval_hours, and .next_crawl_at so a
    plain namespace is sufficient and avoids SQLAlchemy instrumentation noise.
    """
    return types.SimpleNamespace(
        active=active,
        interval_hours=interval_hours,
        next_crawl_at=next_crawl_at,
    )


NOW = datetime(2026, 7, 7, 12, 0, 0)


# ---------------------------------------------------------------------------
# "inactive" branch
# ---------------------------------------------------------------------------

class TestTargetStateInactive:
    def test_inactive_target_returns_inactive_regardless_of_timestamps(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(active=False, next_crawl_at=NOW - timedelta(days=30))
        assert target_state(t, NOW) == "inactive"

    def test_inactive_target_with_none_next_crawl(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(active=False, next_crawl_at=None)
        assert target_state(t, NOW) == "inactive"

    def test_inactive_target_with_future_next_crawl(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(active=False, next_crawl_at=NOW + timedelta(hours=1))
        assert target_state(t, NOW) == "inactive"


# ---------------------------------------------------------------------------
# "ok" branch — next crawl is in the future
# ---------------------------------------------------------------------------

class TestTargetStateOk:
    def test_future_next_crawl_is_ok(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(next_crawl_at=NOW + timedelta(hours=1))
        assert target_state(t, NOW) == "ok"

    def test_far_future_next_crawl_is_ok(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(interval_hours=24, next_crawl_at=NOW + timedelta(hours=23))
        assert target_state(t, NOW) == "ok"

    def test_one_second_in_future_is_ok(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(next_crawl_at=NOW + timedelta(seconds=1))
        assert target_state(t, NOW) == "ok"


# ---------------------------------------------------------------------------
# "due" branch — next_crawl_at <= now (but still within the overdue grace window)
# ---------------------------------------------------------------------------

class TestTargetStateDue:
    def test_next_crawl_at_exactly_now_is_due(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(interval_hours=24, next_crawl_at=NOW)
        assert target_state(t, NOW) == "due"

    def test_next_crawl_at_one_second_ago_is_due(self):
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(interval_hours=24, next_crawl_at=NOW - timedelta(seconds=1))
        assert target_state(t, NOW) == "due"

    def test_next_crawl_at_just_inside_grace_is_due(self):
        """Just inside 0.5×24h = 12h grace window → still "due", not "overdue"."""
        from audiobiblio.acquire.crawler import target_state
        # 12h − 1 second late → within grace
        delta = timedelta(hours=12) - timedelta(seconds=1)
        t = _make_target(interval_hours=24, next_crawl_at=NOW - delta)
        assert target_state(t, NOW) == "due"

    def test_none_next_crawl_at_is_due_for_active_target(self):
        """next_crawl_at=None on an active target means it has never run → treat as due."""
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(active=True, next_crawl_at=None)
        assert target_state(t, NOW) == "due"

    def test_due_uses_explicit_now_not_wall_clock(self):
        """The function must accept an explicit 'now' and not call datetime.utcnow()."""
        from audiobiblio.acquire.crawler import target_state
        past_now = datetime(2020, 1, 1, 0, 0, 0)
        # next_crawl_at is in what would be the future if wall-clock were used,
        # but it is in the past relative to 'past_now' argument.
        t = _make_target(interval_hours=1, next_crawl_at=datetime(2019, 12, 31))
        assert target_state(t, past_now) == "overdue"


# ---------------------------------------------------------------------------
# "overdue" branch — missed by more than 0.5 × interval_hours
# ---------------------------------------------------------------------------

class TestTargetStateOverdue:
    def test_exactly_at_grace_boundary_is_due_not_overdue(self):
        """Spec: overdue when next_crawl_at < now − 0.5×interval (strict) → boundary is still due."""
        from audiobiblio.acquire.crawler import target_state
        # 0.5 × 24h = 12h exactly past due → strict '<' means still "due"
        t = _make_target(interval_hours=24, next_crawl_at=NOW - timedelta(hours=12))
        assert target_state(t, NOW) == "due"

    def test_past_grace_window_is_overdue(self):
        from audiobiblio.acquire.crawler import target_state
        # 20h past due, interval 24h → 0.5×24=12h grace → overdue
        t = _make_target(interval_hours=24, next_crawl_at=NOW - timedelta(hours=20))
        assert target_state(t, NOW) == "overdue"

    def test_overdue_respects_custom_interval(self):
        """Grace window scales with interval_hours."""
        from audiobiblio.acquire.crawler import target_state
        # interval=6h → grace=3h
        # 4h past due → overdue
        t = _make_target(interval_hours=6, next_crawl_at=NOW - timedelta(hours=4))
        assert target_state(t, NOW) == "overdue"

    def test_just_past_grace_boundary_is_overdue(self):
        from audiobiblio.acquire.crawler import target_state
        # 12h + 1s past due (interval 24h) → overdue
        delta = timedelta(hours=12) + timedelta(seconds=1)
        t = _make_target(interval_hours=24, next_crawl_at=NOW - delta)
        assert target_state(t, NOW) == "overdue"

    def test_overdue_with_small_interval(self):
        """interval_hours=1 → grace=30min; 31min past due → overdue."""
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(interval_hours=1, next_crawl_at=NOW - timedelta(minutes=31))
        assert target_state(t, NOW) == "overdue"

    def test_overdue_within_grace_is_not_overdue(self):
        """interval_hours=1 → grace=30min; 29min past due → due."""
        from audiobiblio.acquire.crawler import target_state
        t = _make_target(interval_hours=1, next_crawl_at=NOW - timedelta(minutes=29))
        assert target_state(t, NOW) == "due"


# ---------------------------------------------------------------------------
# Return value is a string literal (not an enum or other type)
# ---------------------------------------------------------------------------

class TestTargetStateReturnType:
    @pytest.mark.parametrize("expected", ["inactive", "ok", "due", "overdue"])
    def test_return_value_is_str(self, expected):
        from audiobiblio.acquire.crawler import target_state

        if expected == "inactive":
            t = _make_target(active=False)
        elif expected == "ok":
            t = _make_target(next_crawl_at=NOW + timedelta(hours=1))
        elif expected == "due":
            t = _make_target(next_crawl_at=NOW)
        else:  # overdue
            t = _make_target(interval_hours=24, next_crawl_at=NOW - timedelta(hours=13))

        result = target_state(t, NOW)
        assert isinstance(result, str), f"expected str, got {type(result)}"
        assert result == expected
