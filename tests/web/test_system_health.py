"""/system health section: each detector's count, 7-day trend, top items."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from audiobiblio.core.db.models import HealthSnapshot
from audiobiblio.core.time import utcnow
from tests.web.test_system import db_session, view_client  # noqa: F401  (fixtures)


def _snap(db, days_ago, detector, count, items=(), groups=None, error=None):
    day = (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    db.add(HealthSnapshot(day=day, detector=detector, count=count, severity="warn",
                          keys={}, details={"items": list(items), "groups": groups or {}},
                          error=error, notified=False))
    db.commit()


def test_health_section_shows_counts_trend_and_links(view_client, db_session):  # noqa: F811
    _snap(db_session, 7, "expiring", 2)
    _snap(db_session, 0, "expiring", 5, items=[{
        "severity": "crit", "key": "episode:9",
        "message": "Baarová — díl 3 vyprší za 2 dny", "link": "http://nasx:8321/works/42"}])
    _snap(db_session, 0, "librarian", 3, groups={"WAITING-METADATA": 2, "AUTHOR-CHECK": 1})
    _snap(db_session, 0, "disk", 0, error="boom: no such column")
    html = view_client.get("/system").text
    assert "Zdraví" in html
    assert "Baarová — díl 3 vyprší za 2 dny" in html
    assert "http://nasx:8321/works/42" in html
    assert "+3" in html                      # 5 vs 2 a week ago
    assert "WAITING-METADATA" in html
    assert "boom: no such column" in html


def test_health_section_without_snapshots(view_client):  # noqa: F811
    html = view_client.get("/system").text
    assert "Zdraví" in html
    assert "zatím neproběhla" in html


def test_health_check_can_be_triggered(view_client):  # noqa: F811
    with patch("audiobiblio.web.routers.system._run_health_check_bg") as bg:
        r = view_client.post("/api/v1/system/health-check")
    assert r.status_code == 202
    bg.assert_called_once()
