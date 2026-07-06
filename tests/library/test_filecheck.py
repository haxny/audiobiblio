"""
Tests for audiobiblio.library.filecheck

TDD — write tests first (RED), implement to make them GREEN.

Covers:
  1. Existing files stay COMPLETE; missing files are reported (dry mode, no DB changes).
  2. With fix=True: missing files → status=MISSING + stash in extra["last_known_path"].
  3. Extra-dict merge doesn't clobber existing keys.
  4. Limit is respected.
  5. Check all asset types with file_path (not just AUDIO).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode
from audiobiblio.library.filecheck import FileCheckReport, verify_asset_paths


# ---------------------------------------------------------------------------
# Test 1: Existing file stays COMPLETE
# ---------------------------------------------------------------------------

def test_existing_file_stays_complete(db_session, episode_factory, tmp_path: Path) -> None:
    """An existing file should be reported as OK and remain COMPLETE."""
    ep: Episode = episode_factory()
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"fake audio")

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=str(audio_file),
    )
    db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    assert report.checked == 1
    assert report.ok == 1
    assert report.missing == []

    # Verify asset still COMPLETE in DB
    db_session.refresh(asset)
    assert asset.status == AssetStatus.COMPLETE


# ---------------------------------------------------------------------------
# Test 2: Missing file is reported (dry mode, no DB change)
# ---------------------------------------------------------------------------

def test_missing_file_reported_dry_mode(db_session, episode_factory) -> None:
    """A missing file should be reported but not modified in dry mode."""
    ep: Episode = episode_factory()
    missing_path = "/tmp/nonexistent-file-12345.m4a"

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=missing_path,
    )
    db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    assert report.checked == 1
    assert report.ok == 0
    assert len(report.missing) == 1
    assert report.missing[0] == (asset.id, missing_path)

    # Verify asset still COMPLETE (no change in dry mode)
    db_session.refresh(asset)
    assert asset.status == AssetStatus.COMPLETE


# ---------------------------------------------------------------------------
# Test 3: fix=True sets MISSING + stashes last_known_path
# ---------------------------------------------------------------------------

def test_fix_true_sets_missing_and_stashes_path(db_session, episode_factory) -> None:
    """With fix=True, missing files should be marked MISSING and path stashed in extra."""
    ep: Episode = episode_factory()
    missing_path = "/tmp/dead-file.m4a"

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=missing_path,
    )
    db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, fix=True)

    assert report.checked == 1
    assert report.ok == 0
    assert len(report.missing) == 1

    # Verify asset was updated: status=MISSING, path stashed
    db_session.refresh(asset)
    assert asset.status == AssetStatus.MISSING
    assert asset.file_path == missing_path  # file_path untouched
    assert asset.extra is not None
    assert asset.extra["last_known_path"] == missing_path


# ---------------------------------------------------------------------------
# Test 4: Extra-dict merge doesn't clobber existing keys
# ---------------------------------------------------------------------------

def test_extra_dict_merge_preserves_existing_keys(db_session, episode_factory) -> None:
    """Stashing last_known_path should not clobber existing extra fields."""
    ep: Episode = episode_factory()
    missing_path = "/tmp/dead-file.m4a"

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=missing_path,
        extra={"old_key": "old_value", "another": 42},
    )
    db_session.add(asset)
    db_session.commit()

    verify_asset_paths(db_session, fix=True)

    db_session.refresh(asset)
    assert asset.extra["old_key"] == "old_value"
    assert asset.extra["another"] == 42
    assert asset.extra["last_known_path"] == missing_path


# ---------------------------------------------------------------------------
# Test 5: Limit is respected
# ---------------------------------------------------------------------------

def test_limit_respected(db_session, episode_factory) -> None:
    """Only --limit assets should be checked."""
    # Create 5 missing assets
    for i in range(5):
        ep = episode_factory()
        asset = Asset(
            episode_id=ep.id,
            type=AssetType.AUDIO,
            status=AssetStatus.COMPLETE,
            file_path=f"/tmp/missing-{i}.m4a",
        )
        db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, limit=2, fix=False)

    assert report.checked == 2
    assert len(report.missing) == 2


# ---------------------------------------------------------------------------
# Test 6: Check different asset types (not just AUDIO)
# ---------------------------------------------------------------------------

def test_check_all_asset_types_with_file_path(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """Should check META_JSON, WEBPAGE, and other types with file_path."""
    ep: Episode = episode_factory()

    # Create assets of different types
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"audio")

    meta_missing = "/tmp/meta-missing.json"
    webpage_file = tmp_path / "webpage.html"
    webpage_file.write_bytes(b"<html></html>")

    asset_audio = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=str(audio_file),
    )
    asset_meta = Asset(
        episode_id=ep.id,
        type=AssetType.META_JSON,
        status=AssetStatus.COMPLETE,
        file_path=meta_missing,
    )
    asset_webpage = Asset(
        episode_id=ep.id,
        type=AssetType.WEBPAGE,
        status=AssetStatus.COMPLETE,
        file_path=str(webpage_file),
    )

    db_session.add_all([asset_audio, asset_meta, asset_webpage])
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    assert report.checked == 3
    assert report.ok == 2
    assert len(report.missing) == 1
    assert report.missing[0] == (asset_meta.id, meta_missing)


# ---------------------------------------------------------------------------
# Test 7: Non-COMPLETE assets are skipped
# ---------------------------------------------------------------------------

def test_non_complete_assets_skipped(db_session, episode_factory) -> None:
    """Only COMPLETE assets should be checked; MISSING, QUEUED, etc. are ignored."""
    ep1: Episode = episode_factory()
    ep2: Episode = episode_factory()

    asset_missing = Asset(
        episode_id=ep1.id,
        type=AssetType.AUDIO,
        status=AssetStatus.MISSING,
        file_path="/tmp/some-file.m4a",
    )
    asset_queued = Asset(
        episode_id=ep2.id,
        type=AssetType.AUDIO,
        status=AssetStatus.QUEUED,
        file_path="/tmp/some-file2.m4a",
    )

    db_session.add_all([asset_missing, asset_queued])
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    assert report.checked == 0  # Neither should be checked


# ---------------------------------------------------------------------------
# Test 8: Assets without file_path are skipped
# ---------------------------------------------------------------------------

def test_assets_without_file_path_skipped(db_session, episode_factory) -> None:
    """Assets with NULL file_path should be skipped."""
    ep: Episode = episode_factory()

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=None,  # No file path
    )

    db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    assert report.checked == 0


# ---------------------------------------------------------------------------
# Test 9: Path expansion (~ and environment variables)
# ---------------------------------------------------------------------------

def test_path_expansion(db_session, episode_factory, tmp_path: Path, monkeypatch) -> None:
    """Should expand ~ and environment variables in paths."""
    import os

    # Create a test directory in HOME-like location
    ep: Episode = episode_factory()

    # Create a real file at a known location
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"audio")

    # Set an env var and use it in the path
    monkeypatch.setenv("TEST_AUDIO_DIR", str(tmp_path))
    path_with_env = "$TEST_AUDIO_DIR/audio.m4a"

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=path_with_env,
    )

    db_session.add(asset)
    db_session.commit()

    report = verify_asset_paths(db_session, fix=False)

    # Path should be expanded and found
    assert report.checked == 1
    assert report.ok == 1
    assert report.missing == []


# ---------------------------------------------------------------------------
# Test 10: Dry-run does not mutate extra dict
# ---------------------------------------------------------------------------

def test_dry_run_does_not_mutate_extra(db_session, episode_factory) -> None:
    """In dry mode (fix=False), the extra dict should remain unchanged even for missing files."""
    ep: Episode = episode_factory()
    missing_path = "/tmp/nonexistent-dry-run.m4a"

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=missing_path,
        extra={"existing": 1},
    )
    db_session.add(asset)
    db_session.commit()

    # Run in dry-run mode
    verify_asset_paths(db_session, fix=False)

    # Verify extra dict is unchanged
    db_session.refresh(asset)
    assert asset.extra == {"existing": 1}
    assert "last_known_path" not in asset.extra
