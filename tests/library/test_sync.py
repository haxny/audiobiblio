"""Tests for audiobiblio.library.sync — DB-resolved tags projected onto audio files.

TDD: 5 cases per brief:
1. no-provenance no-diff: no MetadataValue rows, file matches ORM fallback → all action="none"
2. scraped-only DB vs richer file → FILE recorded, file value wins, action="record_file"
3. MANUAL in DB vs different file → action="rewrite"; --write updates file
4. idempotent second run → zero diffs (all action="none")
5. unreadable/missing file → SyncReport returned with note, no exception
"""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Episode,
    FieldOrigin,
    MetadataValue,
    Work,
)
from audiobiblio.library.sync import (
    DB_TO_TAG,
    FieldDiff,
    SyncReport,
    compute_resolved,
    sync_episode_tags,
)
from audiobiblio.tags.writer import write_tags
from audiobiblio.tags.reader import read_tags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_audio_asset(session, episode: Episode, path: str) -> Asset:
    """Register a COMPLETE AUDIO asset for an episode."""
    asset = Asset(
        episode_id=episode.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=path,
    )
    session.add(asset)
    session.flush()
    return asset


def _add_mv(
    session,
    entity_type: str,
    entity_id: int,
    field: str,
    value: str,
    origin: FieldOrigin,
    source: str = "test",
) -> MetadataValue:
    mv = MetadataValue(
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        value=value,
        origin=origin,
        source=source,
    )
    session.add(mv)
    session.flush()
    return mv


# ---------------------------------------------------------------------------
# Test 1: no-provenance no-diff
# ---------------------------------------------------------------------------

def test_no_provenance_all_empty_no_diff(
    db_session, episode_factory, silent_m4a: Path
) -> None:
    """No MetadataValue rows + file with no tags → all resolved values empty
    and all file values empty → every field action='none'.
    """
    ep: Episode = episode_factory()
    # Override title to empty so ORM fallback also empty
    ep.title = ""
    work = db_session.get(Work, ep.work_id)
    work.author = None
    work.year = None
    db_session.flush()

    _add_audio_asset(db_session, ep, str(silent_m4a))

    # The fresh silent M4A has no tags → all file values = ""
    # With title="" and author=None, ORM fallback = "" for all fields
    report = sync_episode_tags(db_session, ep, write=False)

    assert isinstance(report, SyncReport)
    assert report.episode_id == ep.id
    assert report.note == ""

    actions = {d.field: d.action for d in report.diffs}
    # All fields must be "none" (empty == empty)
    for field in DB_TO_TAG:
        assert actions.get(field) == "none", (
            f"field '{field}' expected action='none', got {actions.get(field)!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: scraped-only DB vs richer file → record_file, no rewrite
# ---------------------------------------------------------------------------

def test_scraped_db_vs_richer_file_records_file_wins(
    db_session, episode_factory, silent_m4a: Path
) -> None:
    """DB has SCRAPED 'scraped_title' for episode.title.
    File has 'richer_title'. FILE rank > SCRAPED → file value wins.
    Action should be 'record_file' (no rewrite needed).
    """
    ep: Episode = episode_factory()
    ep.title = ""
    work = db_session.get(Work, ep.work_id)
    work.author = None
    work.year = None
    db_session.flush()

    _add_mv(db_session, "episode", ep.id, "title", "scraped_title", FieldOrigin.SCRAPED, "mujrozhlas")
    _add_audio_asset(db_session, ep, str(silent_m4a))

    # Write 'richer_title' to file tags
    write_tags(str(silent_m4a), {}, {"title": "richer_title"})

    report = sync_episode_tags(db_session, ep, write=False)

    assert report.note == ""
    title_diff = next(d for d in report.diffs if d.field == "title")
    assert title_diff.file_value == "richer_title"
    assert title_diff.resolved_value == "richer_title"  # FILE wins over SCRAPED
    assert title_diff.action == "record_file"

    # Verify the FILE MetadataValue was recorded
    file_mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="title",
            origin=FieldOrigin.FILE,
        )
        .first()
    )
    assert file_mv is not None
    assert file_mv.value == "richer_title"


# ---------------------------------------------------------------------------
# Test 3: MANUAL in DB vs different file → rewrite action + write=True applies
# ---------------------------------------------------------------------------

def test_manual_db_vs_different_file_rewrites(
    db_session, episode_factory, silent_m4a: Path
) -> None:
    """DB has MANUAL 'manual_title' for episode.title.
    File has 'file_title'. MANUAL rank (4) > FILE rank (2) → rewrite needed.
    With write=True, file is updated to 'manual_title'.
    """
    ep: Episode = episode_factory()
    ep.title = ""
    work = db_session.get(Work, ep.work_id)
    work.author = None
    work.year = None
    db_session.flush()

    _add_mv(db_session, "episode", ep.id, "title", "manual_title", FieldOrigin.MANUAL, "user")
    _add_audio_asset(db_session, ep, str(silent_m4a))

    # Write 'file_title' to file tags
    write_tags(str(silent_m4a), {}, {"title": "file_title"})

    # Dry run: should report rewrite but not apply it
    report_dry = sync_episode_tags(db_session, ep, write=False)
    title_diff = next(d for d in report_dry.diffs if d.field == "title")
    assert title_diff.file_value == "file_title"
    assert title_diff.resolved_value == "manual_title"
    assert title_diff.action == "rewrite"

    # Actual write
    report_write = sync_episode_tags(db_session, ep, write=True)
    title_diff_w = next(d for d in report_write.diffs if d.field == "title")
    assert title_diff_w.action == "rewrite"

    # Verify file was updated
    tags_after = read_tags(str(silent_m4a))
    assert tags_after.get("title") == "manual_title"


# ---------------------------------------------------------------------------
# Test 4: idempotent second run → zero diffs
# ---------------------------------------------------------------------------

def test_idempotent_second_run(
    db_session, episode_factory, silent_m4a: Path
) -> None:
    """After a write=True sync, a second run finds no diffs to act on."""
    ep: Episode = episode_factory()
    ep.title = ""
    work = db_session.get(Work, ep.work_id)
    work.author = None
    work.year = None
    db_session.flush()

    _add_mv(db_session, "episode", ep.id, "title", "canonical_title", FieldOrigin.MANUAL, "user")
    _add_audio_asset(db_session, ep, str(silent_m4a))

    # File has different value
    write_tags(str(silent_m4a), {}, {"title": "stale_title"})

    # First run with write=True
    report1 = sync_episode_tags(db_session, ep, write=True)
    rewrite_diffs = [d for d in report1.diffs if d.action == "rewrite"]
    assert len(rewrite_diffs) == 1  # title was rewritten

    # Second run: all should be "none" (no rewrites, no recordings)
    report2 = sync_episode_tags(db_session, ep, write=False)
    non_none = [d for d in report2.diffs if d.action != "none"]
    assert non_none == [], f"Expected zero non-none diffs on second run, got: {non_none}"


# ---------------------------------------------------------------------------
# Test 5: unreadable / missing file → no crash, note in report
# ---------------------------------------------------------------------------

def test_missing_file_returns_report_with_note(
    db_session, episode_factory
) -> None:
    """If the audio file doesn't exist, sync_episode_tags returns a SyncReport
    with an explanatory note and empty diffs — no exception raised.
    """
    ep: Episode = episode_factory()
    _add_audio_asset(db_session, ep, "/nonexistent/path/to/audio.m4a")

    report = sync_episode_tags(db_session, ep, write=False)

    assert isinstance(report, SyncReport)
    assert report.diffs == []
    assert "missing" in report.note.lower() or "audio" in report.note.lower()


def test_no_audio_asset_returns_report_with_note(
    db_session, episode_factory
) -> None:
    """If there's no COMPLETE audio asset, sync_episode_tags returns an empty
    SyncReport with a note about the missing asset.
    """
    ep: Episode = episode_factory()
    # No asset added

    report = sync_episode_tags(db_session, ep, write=False)

    assert isinstance(report, SyncReport)
    assert report.diffs == []
    assert report.note != ""


# ---------------------------------------------------------------------------
# Additional: compute_resolved falls back to ORM values
# ---------------------------------------------------------------------------

def test_compute_resolved_orm_fallback(db_session, episode_factory) -> None:
    """With no MetadataValue rows, compute_resolved falls back to ORM values."""
    ep: Episode = episode_factory()
    work = db_session.get(Work, ep.work_id)
    work.author = "Test Author"
    work.year = 2023
    db_session.flush()

    resolved = compute_resolved(db_session, ep)

    # title: ORM fallback = episode.title
    assert resolved["title"] == ep.title
    # author: ORM fallback = work.author
    assert resolved["author"] == "Test Author"
    # year: ORM fallback = work.year as string
    assert resolved["year"] == "2023"
