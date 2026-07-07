"""
test_import_api — Tests for the import scan & review API.

Covers: scan (task submission), list findings (filter, invalid bucket),
accept (new, non-new, bad episode_id, episode_id assignment),
ignore (new, non-new), and route census for /import page.
"""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import (
    Episode,
    ImportBucket,
    ImportFinding,
    Program,
    Series,
    Station,
    Work,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_episode(db) -> Episode:
    st = Station(code="IMPT", name="Import Test Station")
    db.add(st)
    db.flush()
    pg = Program(station_id=st.id, name="Import Test Program")
    db.add(pg)
    db.flush()
    sr = Series(program_id=pg.id, name="Import Test Series")
    db.add(sr)
    db.flush()
    wk = Work(series_id=sr.id, title="Import Test Work", author="Author")
    db.add(wk)
    db.flush()
    ep = Episode(work_id=wk.id, title="Import Episode", url="https://example.com/import-ep")
    db.add(ep)
    db.flush()
    return ep


def _make_finding(
    db,
    bucket: ImportBucket = ImportBucket.MATCHED,
    status: str = "new",
    episode: Episode | None = None,
    path: str = "/some/audio.m4a",
) -> ImportFinding:
    f = ImportFinding(
        scan_id="test-scan-id-001",
        path=path,
        bucket=bucket,
        episode_id=episode.id if episode else None,
        details={},
        status=status,
    )
    db.add(f)
    db.flush()
    return f


# ---------------------------------------------------------------------------
# Test: POST /api/v1/import/scan
# ---------------------------------------------------------------------------

class TestScan:
    def test_scan_returns_202_with_task_id(self, client, db_session, monkeypatch):
        import audiobiblio.web.tasks as tasks_mod
        monkeypatch.setattr(
            tasks_mod.task_tracker,
            "submit",
            lambda name, fn, *a, **kw: "test-task-id",
        )

        r = client.post("/api/v1/import/scan", json={"root": None, "inbox": False})
        assert r.status_code == 202
        data = r.json()
        assert data["task_id"] == "test-task-id"
        assert data["name"] == "import_scan"
        assert data["status"] == "running"

    def test_scan_calls_task_tracker_submit(self, client, db_session, monkeypatch):
        import audiobiblio.web.tasks as tasks_mod

        calls: list[dict] = []

        def fake_submit(name, fn, *a, **kw):
            calls.append({"name": name, "args": a})
            return "fake-task-id"

        monkeypatch.setattr(tasks_mod.task_tracker, "submit", fake_submit)

        r = client.post("/api/v1/import/scan", json={"root": None, "inbox": False})
        assert r.status_code == 202
        assert len(calls) == 1
        assert calls[0]["name"] == "import_scan"

    def test_scan_inbox_with_no_inbox_dirs_returns_400(self, client, db_session, monkeypatch):
        """inbox=true with empty cfg.inbox_dirs must 400 — never silently scan library_dir."""
        import audiobiblio.web.routers.importer as importer_mod
        import audiobiblio.web.tasks as tasks_mod
        from audiobiblio.core.config import Config

        monkeypatch.setattr(importer_mod, "load_config", lambda: Config(inbox_dirs=[]))

        calls: list[str] = []
        monkeypatch.setattr(
            tasks_mod.task_tracker,
            "submit",
            lambda name, fn, *a, **kw: calls.append(name) or "unused-task-id",
        )

        r = client.post("/api/v1/import/scan", json={"root": None, "inbox": True})
        assert r.status_code == 400
        assert calls == []


# ---------------------------------------------------------------------------
# Test: GET /api/v1/import/findings
# ---------------------------------------------------------------------------

class TestListFindings:
    def test_empty_list_when_no_findings(self, client, db_session):
        r = client.get("/api/v1/import/findings")
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_filters_by_bucket_matched(self, client, db_session):
        ep = _make_episode(db_session)
        _make_finding(db_session, bucket=ImportBucket.MATCHED, episode=ep, path="/a.m4a")
        _make_finding(db_session, bucket=ImportBucket.UNKNOWN, path="/b.m4a")
        db_session.commit()

        r = client.get("/api/v1/import/findings?bucket=matched")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["bucket"] == "matched"

    def test_invalid_bucket_returns_400(self, client, db_session):
        r = client.get("/api/v1/import/findings?bucket=invalid_bucket")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Test: POST /api/v1/import/findings/{id}/accept
# ---------------------------------------------------------------------------

class TestAcceptFinding:
    def test_accept_new_finding_returns_200(self, client, db_session, monkeypatch):
        ep = _make_episode(db_session)
        finding = _make_finding(db_session, bucket=ImportBucket.MATCHED, episode=ep)
        db_session.commit()

        import audiobiblio.web.routers.importer as importer_mod
        monkeypatch.setattr(importer_mod, "accept_finding", lambda s, f, **kw: [])

        r = client.post(
            f"/api/v1/import/findings/{finding.id}/accept",
            json={"move": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_accept_non_new_finding_returns_409(self, client, db_session):
        ep = _make_episode(db_session)
        finding = _make_finding(
            db_session, bucket=ImportBucket.MATCHED, episode=ep, status="accepted"
        )
        db_session.commit()

        r = client.post(
            f"/api/v1/import/findings/{finding.id}/accept",
            json={"move": False},
        )
        assert r.status_code == 409

    def test_accept_with_nonexistent_episode_id_returns_404(self, client, db_session):
        finding = _make_finding(db_session, bucket=ImportBucket.UNKNOWN)
        db_session.commit()

        r = client.post(
            f"/api/v1/import/findings/{finding.id}/accept",
            json={"move": False, "episode_id": 99999},
        )
        assert r.status_code == 404

    def test_accept_with_valid_episode_id_updates_finding(self, client, db_session, monkeypatch):
        ep = _make_episode(db_session)
        finding = _make_finding(
            db_session, bucket=ImportBucket.UNKNOWN, path="/unknown.m4a"
        )
        db_session.commit()

        import audiobiblio.web.routers.importer as importer_mod
        monkeypatch.setattr(importer_mod, "accept_finding", lambda s, f, **kw: [])

        r = client.post(
            f"/api/v1/import/findings/{finding.id}/accept",
            json={"move": False, "episode_id": ep.id},
        )
        assert r.status_code == 200
        # finding.episode_id is updated in-session (shared session object)
        assert finding.episode_id == ep.id


# ---------------------------------------------------------------------------
# Test: POST /api/v1/import/findings/{id}/ignore
# ---------------------------------------------------------------------------

class TestIgnoreFinding:
    def test_ignore_new_finding_returns_200(self, client, db_session, monkeypatch):
        finding = _make_finding(db_session, bucket=ImportBucket.UNKNOWN)
        db_session.commit()

        import audiobiblio.web.routers.importer as importer_mod
        monkeypatch.setattr(importer_mod, "ignore_finding", lambda s, f: None)

        r = client.post(f"/api/v1/import/findings/{finding.id}/ignore")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    def test_ignore_non_new_finding_returns_409(self, client, db_session):
        finding = _make_finding(
            db_session, bucket=ImportBucket.UNKNOWN, status="ignored"
        )
        db_session.commit()

        r = client.post(f"/api/v1/import/findings/{finding.id}/ignore")
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Test: Route census
# ---------------------------------------------------------------------------

def test_import_page_route_registered():
    """/import appears in the views router."""
    from audiobiblio.web.views import router as views_router
    paths = [getattr(r, "path", None) for r in views_router.routes]
    assert "/import" in paths
