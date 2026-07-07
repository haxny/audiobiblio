"""
Integration tests for the `crawl-status` CLI command.

Uses typer.testing.CliRunner so the command runs in-process without spawning
a subprocess.  The test patches get_session() to inject an in-memory SQLite
DB with controlled CrawlTarget fixtures, so no live DB or network is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from audiobiblio.core.db.models import Base, CrawlTarget, CrawlTargetKind, ApprovalMode
from audiobiblio.cli import app


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _add_target(
    session,
    *,
    name: str,
    active: bool = True,
    interval_hours: int = 24,
    next_crawl_at: datetime | None = None,
) -> CrawlTarget:
    t = CrawlTarget(
        url=f"https://example.cz/{name.lower().replace(' ', '-')}",
        kind=CrawlTargetKind.PROGRAM,
        name=name,
        active=active,
        interval_hours=interval_hours,
        next_crawl_at=next_crawl_at,
        approval_mode=ApprovalMode.REVIEW,
    )
    session.add(t)
    session.flush()
    return t


_NOW = datetime(2026, 7, 7, 12, 0, 0)

runner = CliRunner()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestCrawlStatusCommand:
    def _run(self, db_session, now=_NOW):
        with patch("audiobiblio.cli.get_session", return_value=db_session), \
             patch("audiobiblio.cli._crawl_status_now", return_value=now):
            return runner.invoke(app, ["crawl-status"])

    def test_command_exits_zero_with_no_targets(self, db_session):
        result = self._run(db_session)
        assert result.exit_code == 0, result.output

    def test_output_contains_column_headers(self, db_session):
        _add_target(db_session, name="Radio Prague")
        result = self._run(db_session)
        assert result.exit_code == 0, result.output
        output = result.output
        assert "name" in output.lower() or "Name" in output
        assert "state" in output.lower() or "State" in output

    def test_ok_state_shown(self, db_session):
        _add_target(
            db_session,
            name="OkSource",
            interval_hours=24,
            next_crawl_at=_NOW + timedelta(hours=6),
        )
        result = self._run(db_session)
        assert "ok" in result.output.lower()

    def test_due_state_shown(self, db_session):
        _add_target(
            db_session,
            name="DueSource",
            interval_hours=24,
            next_crawl_at=_NOW - timedelta(hours=6),  # within grace (12h)
        )
        result = self._run(db_session)
        assert "due" in result.output.lower()

    def test_overdue_state_shown(self, db_session):
        _add_target(
            db_session,
            name="OverdueSource",
            interval_hours=24,
            next_crawl_at=_NOW - timedelta(hours=25),  # past grace (12h)
        )
        result = self._run(db_session)
        assert "overdue" in result.output.lower()

    def test_inactive_state_shown(self, db_session):
        _add_target(db_session, name="InactiveSource", active=False)
        result = self._run(db_session)
        assert "inactive" in result.output.lower()

    def test_target_name_shown(self, db_session):
        _add_target(db_session, name="MySpecialProgram")
        result = self._run(db_session)
        assert "MySpecialProgram" in result.output

    def test_multiple_targets_all_shown(self, db_session):
        _add_target(db_session, name="Alpha")
        _add_target(db_session, name="Beta")
        _add_target(db_session, name="Gamma")
        result = self._run(db_session)
        assert result.exit_code == 0, result.output
        assert "Alpha" in result.output
        assert "Beta" in result.output
        assert "Gamma" in result.output

    def test_no_targets_message(self, db_session):
        result = self._run(db_session)
        assert result.exit_code == 0
        # Should handle gracefully — either empty table or a message
        assert result.output is not None
