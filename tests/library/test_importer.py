"""
Tests for audiobiblio.library.importer — Import scanner + import_findings table.

TDD — RED (write tests) then GREEN (implement).

Coverage:
  1. parse_stem: 4 NAMING_CONVENTION shapes
  2. scan_directory: matched-by-title (single candidate → MATCHED)
  3. scan_directory: dead-path recovery by basename (MISSING asset) → MATCHED
  4. scan_directory: dead-path recovery by last_known_path basename → MATCHED
  5. scan_directory: duplicate — matched episode already has COMPLETE audio at different path
  6. scan_directory: unknown — no match
  7. scan_directory: generic-title note in details
  8. accept_finding: links new AUDIO asset + records FILE provenance + applies media info
  9. accept_finding: repairs MISSING asset (clears last_known_path)
 10. accept_finding: move=True relocates file to library + provenance records final path
 11. accept_finding: DUPLICATE without trash_fn raises ValueError
 12. accept_finding: DUPLICATE replaces old file via trash + asset updated in place
 13. ignore_finding: sets status=ignored
 14. re-scan idempotence: updates "new" findings, leaves resolved ones untouched
 15. _scope_episodes: finds episodes via Program.name (not only Work.title/author)
 16. scan_directory: global_cap recorded in details when global search is capped
 17. accept_finding: tag-field MetadataValues recorded on plain accept
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Episode,
    ImportBucket,
    ImportFinding,
    MetadataValue,
    Work,
)
from audiobiblio.library.importer import (
    ScanReport,
    _scope_episodes,
    accept_finding,
    ignore_finding,
    parse_stem,
    scan_directory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio_file(path: Path) -> Path:
    """Create a minimal but real MP3-like binary (ID3 header) for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write a minimal valid MP3 file (ID3v2 header + silence frame is complex;
    # use an empty file — importer must not crash on unreadable tags)
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 412)  # minimal MP3-ish bytes
    return path


def _add_complete_audio(session, episode: Episode, file_path: str) -> Asset:
    asset = Asset(
        episode_id=episode.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=file_path,
    )
    session.add(asset)
    session.flush()
    return asset


def _add_missing_audio(
    session, episode: Episode, file_path: str, last_known_path: str | None = None
) -> Asset:
    extra = {}
    if last_known_path:
        extra["last_known_path"] = last_known_path
    asset = Asset(
        episode_id=episode.id,
        type=AssetType.AUDIO,
        status=AssetStatus.MISSING,
        file_path=file_path,
        extra=extra or None,
    )
    session.add(asset)
    session.flush()
    return asset


def _add_audio_with_status(
    session, episode: Episode, file_path: str, status: AssetStatus
) -> Asset:
    """Helper to add an audio asset with a specific status (MISSING, FAILED, STALE, etc.)."""
    asset = Asset(
        episode_id=episode.id,
        type=AssetType.AUDIO,
        status=status,
        file_path=file_path,
    )
    session.add(asset)
    session.flush()
    return asset


# ---------------------------------------------------------------------------
# 1. parse_stem — 4 NAMING_CONVENTION shapes
# ---------------------------------------------------------------------------


class TestParseStem:
    def test_pattern1_basic(self):
        """Pattern 1: Author - Album"""
        result = parse_stem("Otakar Batlicka - Pribehy a prihody")
        assert result["author"] == "Otakar Batlicka"
        assert result["album"] == "Pribehy a prihody"
        assert result.get("year") is None
        assert result.get("track") is None
        assert result.get("title") is None

    def test_pattern2_with_year(self):
        """Pattern 2: Author - (YYYY) Album"""
        result = parse_stem("Otakar Batlicka - (2015) Pribehy a prihody")
        assert result["author"] == "Otakar Batlicka"
        assert result["year"] == "2015"
        assert result["album"] == "Pribehy a prihody"
        assert result.get("track") is None

    def test_pattern3_tracks_no_titles(self):
        """Pattern 3: Author - (YYYY) Album - NN"""
        result = parse_stem("Otakar Batlicka - (2015) Pribehy a prihody - 01")
        assert result["author"] == "Otakar Batlicka"
        assert result["year"] == "2015"
        assert result["album"] == "Pribehy a prihody"
        assert result["track"] == "01"
        assert not result.get("title")

    def test_pattern4_complete(self):
        """Pattern 4: Author - (YYYY) Album - NN Title"""
        result = parse_stem("Otakar Batlicka - (2015) Pribehy a prihody - 01 Strach")
        assert result["author"] == "Otakar Batlicka"
        assert result["year"] == "2015"
        assert result["album"] == "Pribehy a prihody"
        assert result["track"] == "01"
        assert result["title"] == "Strach"

    def test_unparseable_returns_empty(self):
        """Stems that don't match any pattern return {}."""
        assert parse_stem("random_garbage_123") == {}
        assert parse_stem("") == {}


# ---------------------------------------------------------------------------
# 2. Scan: matched by title (single candidate)
# ---------------------------------------------------------------------------


def test_scan_matched_by_title(db_session, episode_factory, tmp_path: Path) -> None:
    """A file whose parsed title matches exactly one episode → MATCHED bucket."""
    ep: Episode = episode_factory()
    ep.title = "Strach"
    db_session.flush()

    # Create an audio file named after the episode
    audio = _make_audio_file(tmp_path / "Unknown - (2015) Work 1 - 01 Strach.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-001")

    assert report.total >= 1
    assert report.matched >= 1

    finding = (
        db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    )
    assert finding is not None
    assert finding.bucket == ImportBucket.MATCHED
    assert finding.episode_id == ep.id
    assert finding.details is not None
    assert finding.details.get("match_reason") == "title"
    assert finding.status == "new"


# ---------------------------------------------------------------------------
# 3. Dead-path recovery — basename match against Asset.file_path of MISSING asset
# ---------------------------------------------------------------------------


def test_scan_dead_path_recovery_file_path(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """A file whose basename matches a MISSING asset's file_path → MATCHED 'path'."""
    ep: Episode = episode_factory()
    old_path = "/old/location/episode_audio.mp3"
    _add_missing_audio(db_session, ep, old_path)

    # Create the "found" file with the same basename
    audio = _make_audio_file(tmp_path / "episode_audio.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-002")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.bucket == ImportBucket.MATCHED
    assert finding.episode_id == ep.id
    assert finding.details.get("match_reason") == "path"


# ---------------------------------------------------------------------------
# 4. Dead-path recovery — basename match against last_known_path of MISSING asset
# ---------------------------------------------------------------------------


def test_scan_dead_path_recovery_last_known(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """File whose basename matches extra['last_known_path'] of a MISSING asset → MATCHED 'path'."""
    ep: Episode = episode_factory()
    _add_missing_audio(
        db_session,
        ep,
        file_path="/current/different/name.mp3",
        last_known_path="/original/location/recovered_audio.mp3",
    )

    audio = _make_audio_file(tmp_path / "recovered_audio.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-003")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.bucket == ImportBucket.MATCHED
    assert finding.episode_id == ep.id
    assert finding.details.get("match_reason") == "path"


# ---------------------------------------------------------------------------
# 5. Duplicate — matched episode already has COMPLETE audio at different path
# ---------------------------------------------------------------------------


def test_scan_duplicate_existing_complete(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """Episode already has a COMPLETE audio at a different path → DUPLICATE bucket."""
    ep: Episode = episode_factory()
    ep.title = "Unique Title Episode"
    db_session.flush()
    _add_complete_audio(db_session, ep, "/library/existing_audio.mp3")

    audio = _make_audio_file(tmp_path / "Unknown - (2015) Work 1 - 01 Unique Title Episode.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-004")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.bucket == ImportBucket.DUPLICATE
    assert finding.episode_id == ep.id


# ---------------------------------------------------------------------------
# 6. Unknown — no match
# ---------------------------------------------------------------------------


def test_scan_unknown_no_match(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """A file that matches no episode → UNKNOWN bucket."""
    _: Episode = episode_factory()  # DB has episodes, but none match

    audio = _make_audio_file(tmp_path / "ZZZZ - (1900) Completely Unknown Work - 01 Nope.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-005")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.bucket == ImportBucket.UNKNOWN


# ---------------------------------------------------------------------------
# 7. Generic-title note in details
# ---------------------------------------------------------------------------


def test_scan_generic_title_noted(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """Files with a generic/placeholder parsed title have it noted in details."""
    _: Episode = episode_factory()

    # "epizody poradu" is a known generic title in dedupe.matching
    audio = _make_audio_file(tmp_path / "SomeAuthor - (2020) Album - 01 epizody poradu.mp3")

    report = scan_directory(db_session, tmp_path, scan_id="scan-006")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.details is not None
    assert finding.details.get("generic_title") is True


# ---------------------------------------------------------------------------
# 8. accept_finding: link new AUDIO asset + FILE provenance + media info
#    Also covers m4: tag-field MetadataValue recorded on plain accept.
# ---------------------------------------------------------------------------


def test_accept_finding_links_audio_asset(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """accept_finding creates a COMPLETE AUDIO asset and records FILE provenance.

    Asserts:
    - file_path MetadataValue recorded with correct (inbox) path.
    - title MetadataValue recorded from finding.details["tags"] (m4).
    """
    ep: Episode = episode_factory()
    ep.title = "Linked Episode"
    db_session.flush()

    audio = _make_audio_file(tmp_path / "audio_link_test.mp3")

    finding = ImportFinding(
        scan_id="scan-007",
        path=str(audio),
        bucket=ImportBucket.MATCHED,
        episode_id=ep.id,
        details={"match_reason": "title", "tags": {"title": "Linked Episode"}},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    with patch("audiobiblio.library.importer.apply_media_info") as mock_mi:
        mock_mi.return_value = None
        accept_finding(db_session, finding)

    db_session.refresh(finding)
    assert finding.status == "accepted"
    assert finding.resolved_at is not None

    asset = (
        db_session.query(Asset)
        .filter_by(episode_id=ep.id, type=AssetType.AUDIO)
        .first()
    )
    assert asset is not None
    assert asset.status == AssetStatus.COMPLETE
    assert asset.file_path == str(audio)

    # file_path provenance recorded
    fp_mv = (
        db_session.query(MetadataValue)
        .filter_by(entity_type="episode", entity_id=ep.id, field="file_path")
        .first()
    )
    assert fp_mv is not None
    assert fp_mv.value == str(audio)

    # title tag provenance recorded (m4)
    title_mv = (
        db_session.query(MetadataValue)
        .filter_by(entity_type="episode", entity_id=ep.id, field="title")
        .first()
    )
    assert title_mv is not None
    assert title_mv.value == "Linked Episode"


# ---------------------------------------------------------------------------
# 9. accept_finding: repairs MISSING asset + clears last_known_path
# ---------------------------------------------------------------------------


def test_accept_finding_repairs_missing_asset(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """accept_finding on a MATCHED finding repairs an existing MISSING asset."""
    ep: Episode = episode_factory()
    old_path = "/old/missing_audio.mp3"
    missing_asset = _add_missing_audio(
        db_session, ep, old_path, last_known_path=old_path
    )

    audio = _make_audio_file(tmp_path / "missing_audio.mp3")

    finding = ImportFinding(
        scan_id="scan-008",
        path=str(audio),
        bucket=ImportBucket.MATCHED,
        episode_id=ep.id,
        details={"match_reason": "path"},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    with patch("audiobiblio.library.importer.apply_media_info") as mock_mi:
        mock_mi.return_value = None
        accept_finding(db_session, finding)

    db_session.refresh(missing_asset)
    assert missing_asset.status == AssetStatus.COMPLETE
    assert missing_asset.file_path == str(audio)
    # last_known_path must be removed
    assert "last_known_path" not in (missing_asset.extra or {})


# ---------------------------------------------------------------------------
# 9b. accept_finding: repairs FAILED/STALE assets (treats identically to MISSING)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asset_status", [
    AssetStatus.MISSING,
    AssetStatus.FAILED,
    AssetStatus.STALE,
])
def test_accept_finding_repairs_failed_stale_assets(
    db_session, episode_factory, tmp_path: Path, asset_status: AssetStatus
) -> None:
    """accept_finding on MATCHED finding repairs FAILED/STALE assets identically to MISSING.

    Asserts:
    - Asset status changes to COMPLETE
    - file_path is updated to the new location
    - last_known_path is cleared from extra (if present)
    """
    ep: Episode = episode_factory()
    old_path = "/old/broken_audio.mp3"
    broken_asset = _add_audio_with_status(
        db_session, ep, old_path, asset_status
    )

    audio = _make_audio_file(tmp_path / "broken_audio.mp3")

    finding = ImportFinding(
        scan_id="scan-repair-broken",
        path=str(audio),
        bucket=ImportBucket.MATCHED,
        episode_id=ep.id,
        details={"match_reason": "path"},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    with patch("audiobiblio.library.importer.apply_media_info") as mock_mi:
        mock_mi.return_value = None
        accept_finding(db_session, finding)

    db_session.refresh(broken_asset)
    assert broken_asset.status == AssetStatus.COMPLETE, (
        f"Asset with status {asset_status} should be repaired to COMPLETE"
    )
    assert broken_asset.file_path == str(audio), (
        f"Asset file_path should be updated to the new location"
    )


# ---------------------------------------------------------------------------
# 10. accept_finding: move=True relocates file + provenance uses final path (C1)
# ---------------------------------------------------------------------------


def test_accept_finding_move_relocates_file(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """With move=True, the file is moved to the library target path.

    Asserts (C1): the file_path MetadataValue holds the FINAL library path,
    not the original inbox path.
    """
    ep: Episode = episode_factory()
    ep.title = "Moved Episode"
    db_session.flush()

    audio = _make_audio_file(tmp_path / "inbox" / "to_move.mp3")
    library_dir = tmp_path / "library"
    library_dir.mkdir()

    finding = ImportFinding(
        scan_id="scan-009",
        path=str(audio),
        bucket=ImportBucket.MATCHED,
        episode_id=ep.id,
        details={"match_reason": "title", "tags": {}},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    def fake_build_paths(ep, work=None, info=None):
        target_dir = library_dir / "Program (tst)"
        return {"base_dir": target_dir, "stem": "moved_episode"}

    with patch("audiobiblio.library.importer.build_paths_for_episode", side_effect=fake_build_paths), \
         patch("audiobiblio.library.importer.apply_media_info") as mock_mi:
        mock_mi.return_value = None
        accept_finding(db_session, finding, move=True, library_dir=library_dir)

    db_session.refresh(finding)
    asset = (
        db_session.query(Asset)
        .filter_by(episode_id=ep.id, type=AssetType.AUDIO)
        .first()
    )
    assert asset is not None
    # Asset path should be the new (library) location, not the inbox path
    assert asset.file_path != str(audio)
    assert Path(asset.file_path).exists()
    # Original file should have been moved away
    assert not audio.exists()

    # C1: file_path MetadataValue must hold the FINAL library path, not the inbox path
    fp_mv = (
        db_session.query(MetadataValue)
        .filter_by(entity_type="episode", entity_id=ep.id, field="file_path")
        .first()
    )
    assert fp_mv is not None
    assert fp_mv.value == asset.file_path, (
        f"Provenance records inbox path ({fp_mv.value!r}) "
        f"instead of final library path ({asset.file_path!r})"
    )


# ---------------------------------------------------------------------------
# 11. accept_finding: DUPLICATE without trash_fn raises ValueError
# ---------------------------------------------------------------------------


def test_accept_duplicate_without_trash_fn_raises(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """Accepting a DUPLICATE without trash_fn must raise ValueError."""
    ep: Episode = episode_factory()
    _add_complete_audio(db_session, ep, "/existing/audio.mp3")

    audio = _make_audio_file(tmp_path / "duplicate_audio.mp3")

    finding = ImportFinding(
        scan_id="scan-010",
        path=str(audio),
        bucket=ImportBucket.DUPLICATE,
        episode_id=ep.id,
        details={"match_reason": "title"},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    with pytest.raises(ValueError, match="trash_fn"):
        accept_finding(db_session, finding)


# ---------------------------------------------------------------------------
# 12. accept_finding: DUPLICATE replaces old file via trash + asset updated in place (C2)
# ---------------------------------------------------------------------------


def test_accept_duplicate_replaces_with_trash(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """DUPLICATE accept: trashes old file, asset updated in place to new path, finding accepted.

    The fake trash_fn moves the old file to a tmp trash dir, mirroring reality.
    Asserts:
    - old file is gone from its original location.
    - trash dir contains the old file (fake trash moved it).
    - asset.file_path == str(new_file), asset.status == COMPLETE.
    - finding.status == "accepted".
    """
    ep: Episode = episode_factory()
    ep.title = "Replaced Episode"
    db_session.flush()

    # Old file currently COMPLETE in library
    old_file = _make_audio_file(tmp_path / "old_audio.mp3")
    _add_complete_audio(db_session, ep, str(old_file))

    # New file found in inbox (the duplicate)
    new_file = _make_audio_file(tmp_path / "new_audio.mp3")

    finding = ImportFinding(
        scan_id="scan-dup",
        path=str(new_file),
        bucket=ImportBucket.DUPLICATE,
        episode_id=ep.id,
        details={"match_reason": "title"},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    # Fake trash: physically moves old file to a tmp trash subdirectory
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()

    def fake_trash(path: Path, library_dir) -> Path:
        dest = trash_dir / path.name
        shutil.move(str(path), str(dest))
        return dest

    with patch("audiobiblio.library.importer.apply_media_info") as mock_mi:
        mock_mi.return_value = None
        accept_finding(db_session, finding, trash_fn=fake_trash)

    # Old file must have been moved to trash by fake_trash
    assert not old_file.exists(), "old file should have been trashed"
    assert (trash_dir / old_file.name).exists(), "old file should be in trash dir"

    # Asset updated in place: points to new file, status COMPLETE
    asset = (
        db_session.query(Asset)
        .filter_by(episode_id=ep.id, type=AssetType.AUDIO)
        .first()
    )
    assert asset is not None
    assert asset.status == AssetStatus.COMPLETE
    assert asset.file_path == str(new_file)

    # Finding accepted
    db_session.refresh(finding)
    assert finding.status == "accepted"
    assert finding.resolved_at is not None


# ---------------------------------------------------------------------------
# 13. ignore_finding: sets status=ignored
# ---------------------------------------------------------------------------


def test_ignore_finding(db_session, episode_factory, tmp_path: Path) -> None:
    """ignore_finding sets the finding status to 'ignored'."""
    ep: Episode = episode_factory()
    audio = _make_audio_file(tmp_path / "ignore_me.mp3")

    finding = ImportFinding(
        scan_id="scan-011",
        path=str(audio),
        bucket=ImportBucket.UNKNOWN,
        episode_id=None,
        details={},
        status="new",
    )
    db_session.add(finding)
    db_session.flush()

    ignore_finding(db_session, finding)

    db_session.refresh(finding)
    assert finding.status == "ignored"
    assert finding.resolved_at is not None


# ---------------------------------------------------------------------------
# 14. Re-scan idempotence
# ---------------------------------------------------------------------------


def test_rescan_updates_new_but_not_resolved(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """Re-scanning: 'new' findings are updated; 'accepted'/'ignored' are left alone."""
    ep: Episode = episode_factory()
    ep.title = "IdempotentEp"
    db_session.flush()

    audio_new = _make_audio_file(tmp_path / "new_finding.mp3")
    audio_resolved = _make_audio_file(tmp_path / "resolved_finding.mp3")

    # Pre-insert a 'new' finding and a 'accepted' finding
    new_finding = ImportFinding(
        scan_id="scan-old",
        path=str(audio_new),
        bucket=ImportBucket.UNKNOWN,
        episode_id=None,
        details={"old": True},
        status="new",
    )
    accepted_finding = ImportFinding(
        scan_id="scan-old",
        path=str(audio_resolved),
        bucket=ImportBucket.MATCHED,
        episode_id=ep.id,
        details={"match_reason": "title"},
        status="accepted",
        resolved_at=datetime(2026, 1, 1),
    )
    db_session.add(new_finding)
    db_session.add(accepted_finding)
    db_session.flush()

    old_accepted_scan_id = accepted_finding.scan_id

    # Re-scan
    scan_directory(db_session, tmp_path, scan_id="scan-new")

    # The 'new' finding should be updated with new scan_id
    db_session.refresh(new_finding)
    assert new_finding.scan_id == "scan-new"

    # The 'accepted' finding should be untouched
    db_session.refresh(accepted_finding)
    assert accepted_finding.scan_id == old_accepted_scan_id
    assert accepted_finding.status == "accepted"


# ---------------------------------------------------------------------------
# 15. _scope_episodes: finds episodes via Program.name (I1)
# ---------------------------------------------------------------------------


def test_scope_episodes_by_program_name(
    db_session, episode_factory
) -> None:
    """_scope_episodes finds episodes whose Series→Program.name matches album.

    The episode's Work.title ("Work 1") does NOT match "UniqueRadioProgram".
    Only the Program.name path should find it — proving the I1 fix works.
    """
    ep = episode_factory(program_name="UniqueRadioProgram")
    # work.title = "Work 1" (from episode_factory) — does NOT match "UniqueRadioProgram"
    ep.title = "DiscoveredEpisode"
    db_session.flush()

    results = _scope_episodes(db_session, album="UniqueRadioProgram", author="")

    ep_ids = [e.id for e in results]
    assert ep.id in ep_ids, (
        "episode was not found via Program.name scope; "
        "Work.title='Work 1' does not match album='UniqueRadioProgram' — "
        "only the Program.name path should find this episode"
    )


# ---------------------------------------------------------------------------
# 16. scan_directory: global_cap recorded in details (I3)
# ---------------------------------------------------------------------------


def test_scan_global_cap_recorded_in_details(
    db_session, episode_factory, tmp_path: Path
) -> None:
    """When _match_by_title signals global_cap_hit, details['global_cap'] is set."""
    from audiobiblio.library.importer import GLOBAL_TITLE_CAP

    # File with a parseable title so _match_by_title is actually called
    audio = _make_audio_file(tmp_path / "SomeAuthor - (2020) SomeAlbum - 01 CapTest.mp3")

    # Mock _match_by_title to signal the global cap was hit
    with patch("audiobiblio.library.importer._match_by_title") as mock_mbt:
        mock_mbt.return_value = ("none", [], True)  # (result_type, ep_ids, global_cap_hit)
        scan_directory(db_session, tmp_path, scan_id="scan-cap")

    finding = db_session.query(ImportFinding).filter_by(path=str(audio)).first()
    assert finding is not None
    assert finding.details.get("global_cap") == GLOBAL_TITLE_CAP
