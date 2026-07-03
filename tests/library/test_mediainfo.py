"""
Tests for audiobiblio.library.mediainfo

TDD — write tests first (RED), implement to make them GREEN.

Covers:
  1. read_media_info() on a real M4A file (uses silent_m4a fixture from conftest).
  2. read_media_info() on an unreadable/corrupt file returns all-None fields.
  3. apply_media_info() fills Asset quality columns and episode.duration_ms.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, Work
from audiobiblio.library.mediainfo import MediaInfo, apply_media_info, read_media_info


# ---------------------------------------------------------------------------
# Test 1: real silent M4A produced by ffmpeg fixture
# ---------------------------------------------------------------------------

def test_read_media_info_real_file(silent_m4a: Path) -> None:
    info = read_media_info(silent_m4a)
    assert isinstance(info, MediaInfo)
    assert info.duration_ms is not None and info.duration_ms > 0
    assert info.sample_rate is not None and info.sample_rate > 0
    assert info.channels is not None and info.channels > 0
    # Container should be detected for .m4a
    assert info.container is not None


# ---------------------------------------------------------------------------
# Test 2: corrupt/unreadable file returns all-None fields, never raises
# ---------------------------------------------------------------------------

def test_unreadable_returns_none_fields(tmp_path: Path) -> None:
    bad = tmp_path / "x.m4a"
    bad.write_bytes(b"not audio")
    info = read_media_info(bad)
    assert isinstance(info, MediaInfo)
    assert info.duration_ms is None
    assert info.bitrate is None
    assert info.channels is None
    assert info.sample_rate is None
    assert info.codec is None
    assert info.container is None


# ---------------------------------------------------------------------------
# Test 3: apply_media_info() fills Asset quality fields + episode.duration_ms
# ---------------------------------------------------------------------------

def test_apply_media_info_fills_asset_and_episode(
    db_session, episode_factory, silent_m4a: Path
) -> None:
    ep: Episode = episode_factory()
    assert ep.duration_ms is None  # precondition: not set yet

    # Create a COMPLETE AUDIO asset for this episode
    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=str(silent_m4a),
    )
    db_session.add(asset)
    db_session.flush()

    apply_media_info(db_session, asset, silent_m4a)

    # Asset quality fields should now be populated
    assert asset.sample_rate is not None and asset.sample_rate > 0
    assert asset.channels is not None and asset.channels > 0
    assert asset.container is not None

    # Episode.duration_ms should have been backfilled from the audio duration
    db_session.refresh(ep)
    assert ep.duration_ms is not None and ep.duration_ms > 0
