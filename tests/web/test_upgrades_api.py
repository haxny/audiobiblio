"""
test_upgrades_api — Tests for the upgrade lifecycle REST API.

Covers: list (status filter), stage (task submission), resolve
(replace / keep_old / dismiss), and illegal-transition 409.

Staging DB writes are exercised by directly manipulating db_session
(the stage endpoint itself submits a background task that opens its own
session, which is integration-tested separately; here we stub the
download and verify the endpoint contract).
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Episode,
    Program,
    Series,
    Station,
    UpgradeCandidate,
    UpgradeStatus,
    Work,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree(db):
    """Insert minimal Station → Program → Series → Work → Episode."""
    st = Station(code="TST", name="Test Station")
    db.add(st)
    db.flush()
    pg = Program(station_id=st.id, name="Test Program")
    db.add(pg)
    db.flush()
    sr = Series(program_id=pg.id, name="Test Series")
    db.add(sr)
    db.flush()
    wk = Work(series_id=sr.id, title="Test Work", author="Author")
    db.add(wk)
    db.flush()
    ep = Episode(work_id=wk.id, title="Episode 1", url="https://example.com/ep1")
    db.add(ep)
    db.flush()
    return ep


def _make_audio_asset(db, ep: Episode, file_path: Path) -> Asset:
    a = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=str(file_path),
        size_bytes=file_path.stat().st_size,
    )
    db.add(a)
    db.flush()
    return a


def _make_candidate(db, ep: Episode, asset: Asset, url="https://example.com/new") -> UpgradeCandidate:
    c = UpgradeCandidate(
        episode_id=ep.id,
        candidate_url=url,
        candidate_duration_ms=3_600_000,
        owned_duration_ms=3_300_000,
        owned_asset_id=asset.id,
        status=UpgradeStatus.PENDING_REVIEW,
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ep_and_asset(db_session, tmp_path):
    """Episode + owned audio file on disk."""
    old_file = tmp_path / "ep1.m4a"
    old_file.write_bytes(b"OLD")
    ep = _make_tree(db_session)
    asset = _make_audio_asset(db_session, ep, old_file)
    return {"ep": ep, "asset": asset, "old_file": old_file, "tmp_path": tmp_path}


@pytest.fixture()
def pending_candidate(db_session, ep_and_asset):
    """A PENDING_REVIEW UpgradeCandidate."""
    ep = ep_and_asset["ep"]
    asset = ep_and_asset["asset"]
    c = _make_candidate(db_session, ep, asset)
    return c


@pytest.fixture()
def staged_candidate(db_session, ep_and_asset):
    """A STAGED UpgradeCandidate with a real file on disk."""
    ep = ep_and_asset["ep"]
    asset = ep_and_asset["asset"]
    tmp_path = ep_and_asset["tmp_path"]

    staged_file = tmp_path / "staged" / "candidate.m4a"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_bytes(b"STAGED")

    c = UpgradeCandidate(
        episode_id=ep.id,
        candidate_url="https://example.com/new",
        candidate_duration_ms=3_600_000,
        owned_duration_ms=3_300_000,
        owned_asset_id=asset.id,
        status=UpgradeStatus.STAGED,
        staged_path=str(staged_file),
    )
    db_session.add(c)
    db_session.commit()
    return {
        "candidate": c,
        "old_file": ep_and_asset["old_file"],
        "staged_file": staged_file,
        "library_dir": tmp_path,
        "asset": asset,
    }


# ---------------------------------------------------------------------------
# Tests: list
# ---------------------------------------------------------------------------

class TestListUpgrades:
    def test_list_all(self, client, db_session, pending_candidate):
        r = client.get("/api/v1/upgrades")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["id"] == pending_candidate.id
        assert item["status"] == "pending_review"
        assert item["candidate_url"] == "https://example.com/new"

    def test_list_filter_by_status_match(self, client, db_session, pending_candidate):
        r = client.get("/api/v1/upgrades?status=pending_review")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_list_filter_by_status_no_match(self, client, db_session, pending_candidate):
        r = client.get("/api/v1/upgrades?status=staged")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_list_invalid_status_400(self, client, db_session):
        r = client.get("/api/v1/upgrades?status=bogus")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Tests: stage
# ---------------------------------------------------------------------------

class TestStageUpgrade:
    def test_stage_returns_202_with_task_id(self, client, db_session, pending_candidate, monkeypatch):
        # Stub task_tracker.submit to be a no-op (background task not tested here)
        import audiobiblio.web.tasks as tasks_mod
        monkeypatch.setattr(tasks_mod.task_tracker, "submit",
                            lambda name, fn, *a, **kw: "fake-task-id")

        r = client.post(f"/api/v1/upgrades/{pending_candidate.id}/stage")
        assert r.status_code == 202
        data = r.json()
        assert data["task_id"] == "fake-task-id"
        assert data["name"] == "stage_upgrade"

    def test_stage_404(self, client, db_session):
        r = client.post("/api/v1/upgrades/9999/stage")
        assert r.status_code == 404

    def test_stage_already_staged_409(self, client, db_session, staged_candidate, monkeypatch):
        import audiobiblio.web.tasks as tasks_mod
        monkeypatch.setattr(tasks_mod.task_tracker, "submit",
                            lambda name, fn, *a, **kw: "fake-task-id")

        candidate = staged_candidate["candidate"]
        r = client.post(f"/api/v1/upgrades/{candidate.id}/stage")
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Tests: resolve/replace
# ---------------------------------------------------------------------------

class TestResolveReplace:
    def test_replace_moves_old_to_trash_and_staged_to_library(
        self, client, db_session, staged_candidate, monkeypatch
    ):
        candidate = staged_candidate["candidate"]
        old_file = staged_candidate["old_file"]
        staged_file = staged_candidate["staged_file"]
        library_dir = staged_candidate["library_dir"]

        # Stub load_config to return a config with tmp library_dir
        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config(
            library_dir=str(library_dir),
            download_dir=str(library_dir / "_dl"),
        ))

        # Stub carry_over_tags to avoid mutagen on empty files
        monkeypatch.setattr(upgrades_mod, "carry_over_tags", lambda old, new, **kw: {"title": "T"})

        r = client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                        json={"decision": "replace"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "replaced"
        assert data["resolved_at"] is not None

        # Staged file gone from staging location (moved to old path)
        assert not staged_file.exists(), "staged file should have been moved to old path"

        # Old path now holds the staged content
        assert old_file.exists(), "old path should now hold the staged content"
        assert old_file.read_bytes() == b"STAGED"

        # Original old file should be in dated trash
        trash_root = library_dir / ".trash"
        trashed = list(trash_root.rglob("ep1.m4a"))
        assert trashed, "original old file should be in trash"

        # Candidate status updated
        db_session.refresh(candidate)
        assert candidate.status == UpgradeStatus.REPLACED

    def test_replace_409_when_not_staged(self, client, db_session, pending_candidate, monkeypatch):
        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config())

        r = client.post(f"/api/v1/upgrades/{pending_candidate.id}/resolve",
                        json={"decision": "replace"})
        assert r.status_code == 409

    def test_replace_calls_carry_over_tags(
        self, client, db_session, staged_candidate, monkeypatch
    ):
        candidate = staged_candidate["candidate"]
        old_file = staged_candidate["old_file"]
        staged_file = staged_candidate["staged_file"]
        library_dir = staged_candidate["library_dir"]

        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod

        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config(
            library_dir=str(library_dir),
            download_dir=str(library_dir / "_dl"),
        ))

        calls = []

        def fake_carry(old, new, **kw):
            calls.append((old, new))
            return {"title": "Episode 1"}

        monkeypatch.setattr(upgrades_mod, "carry_over_tags", fake_carry)

        client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                    json={"decision": "replace"})

        assert len(calls) == 1
        assert calls[0][0] == old_file
        assert calls[0][1] == staged_file


# ---------------------------------------------------------------------------
# Tests: resolve/keep_old
# ---------------------------------------------------------------------------

class TestResolveKeepOld:
    def test_keep_old_trashes_staged_file(
        self, client, db_session, staged_candidate, monkeypatch
    ):
        candidate = staged_candidate["candidate"]
        staged_file = staged_candidate["staged_file"]
        library_dir = staged_candidate["library_dir"]

        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config(
            library_dir=str(library_dir),
            download_dir=str(library_dir / "_dl"),
        ))

        r = client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                        json={"decision": "keep_old"})
        assert r.status_code == 200
        assert r.json()["status"] == "kept_old"

        assert not staged_file.exists(), "staged file should be in trash"

        # Staged file should be in trash
        trash_root = library_dir / ".trash"
        staged_name = staged_file.name
        trashed = list(trash_root.rglob(staged_name))
        assert trashed, f"staged file {staged_name} should be in trash"

        db_session.refresh(candidate)
        assert candidate.status == UpgradeStatus.KEPT_OLD

    def test_keep_old_before_staging_works(
        self, client, db_session, pending_candidate, monkeypatch
    ):
        """keep_old on a PENDING_REVIEW candidate (no staged file) is allowed."""
        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config())

        r = client.post(f"/api/v1/upgrades/{pending_candidate.id}/resolve",
                        json={"decision": "keep_old"})
        assert r.status_code == 200
        assert r.json()["status"] == "kept_old"


# ---------------------------------------------------------------------------
# Tests: resolve/dismiss
# ---------------------------------------------------------------------------

class TestResolveDismiss:
    def test_dismiss_before_staging(
        self, client, db_session, pending_candidate, monkeypatch
    ):
        """dismiss on PENDING_REVIEW candidate (no staged path) is valid."""
        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config())

        r = client.post(f"/api/v1/upgrades/{pending_candidate.id}/resolve",
                        json={"decision": "dismiss"})
        assert r.status_code == 200
        assert r.json()["status"] == "dismissed"

        db_session.refresh(pending_candidate)
        assert pending_candidate.status == UpgradeStatus.DISMISSED

    def test_dismiss_with_staged_trashes_file(
        self, client, db_session, staged_candidate, monkeypatch
    ):
        candidate = staged_candidate["candidate"]
        staged_file = staged_candidate["staged_file"]
        library_dir = staged_candidate["library_dir"]

        from audiobiblio.core.config import Config
        import audiobiblio.web.routers.upgrades as upgrades_mod
        monkeypatch.setattr(upgrades_mod, "load_config", lambda: Config(
            library_dir=str(library_dir),
            download_dir=str(library_dir / "_dl"),
        ))

        r = client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                        json={"decision": "dismiss"})
        assert r.status_code == 200
        assert not staged_file.exists()

        # Staged file should be in trash
        trash_root = library_dir / ".trash"
        staged_name = staged_file.name
        trashed = list(trash_root.rglob(staged_name))
        assert trashed, f"staged file {staged_name} should be in trash"


# ---------------------------------------------------------------------------
# Tests: invalid decision
# ---------------------------------------------------------------------------

class TestInvalidDecision:
    def test_invalid_decision_400(self, client, db_session, pending_candidate):
        r = client.post(f"/api/v1/upgrades/{pending_candidate.id}/resolve",
                        json={"decision": "nuke"})
        assert r.status_code == 400

    def test_resolve_404(self, client, db_session):
        r = client.post("/api/v1/upgrades/9999/resolve", json={"decision": "dismiss"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: double-resolve (already terminal)
# ---------------------------------------------------------------------------

class TestDoubleResolve:
    def test_double_resolve_conflicts(self, client, db_session, pending_candidate):
        """Resolving an already-resolved candidate returns 409."""
        candidate = pending_candidate

        # Resolve it once with keep_old
        r = client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                        json={"decision": "keep_old"})
        assert r.status_code == 200
        assert r.json()["status"] == "kept_old"

        # Try to resolve again with any decision (replace)
        r = client.post(f"/api/v1/upgrades/{candidate.id}/resolve",
                        json={"decision": "replace"})
        assert r.status_code == 409
