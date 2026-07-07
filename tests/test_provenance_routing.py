"""
Cross-component integration: genre lives on the episode entity, not on the work.

Verifies the three-way consistency between:
  - routers/episodes.py  (WORK_FIELDS alias _WORK_ORM_FIELDS: genre → episode ✓)
  - views.py             (WORK_FIELDS alias _WORK_LEVEL_FIELDS: genre → episode ✓)
  - sync.py              (imports WORK_FIELDS from core.provenance: genre → episode ✓)

Tests:
  1. WORK_FIELDS constant — genre absent, author + year present.
  2. MANUAL genre edit → compute_resolved returns it → sync_episode_tags rewrite diff.
  3. Author is WORK-routed counter-case: MANUAL author on work → compute_resolved returns it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Episode,
    FieldOrigin,
    MetadataValue,
    Program,
    Series,
    Station,
    Work,
)
from audiobiblio.core.provenance import WORK_FIELDS, record_value
from audiobiblio.library.sync import compute_resolved, sync_episode_tags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_episode(db_session) -> tuple[Episode, Work]:
    """Create a minimal Station → Program → Series → Work → Episode chain."""
    st = Station(code="PRT", name="Provenance Routing Test")
    db_session.add(st)
    db_session.flush()
    pg = Program(station_id=st.id, name="PRT Program")
    db_session.add(pg)
    db_session.flush()
    sr = Series(program_id=pg.id, name="PRT Series")
    db_session.add(sr)
    db_session.flush()
    wk = Work(series_id=sr.id, title="PRT Work", author="Initial Author")
    db_session.add(wk)
    db_session.flush()
    ep = Episode(work_id=wk.id, title="PRT Episode", url="https://example.com/prt")
    db_session.add(ep)
    db_session.flush()
    return ep, wk


# ---------------------------------------------------------------------------
# Test 1: WORK_FIELDS constant sanity
# ---------------------------------------------------------------------------


def test_work_fields_constant_excludes_genre():
    """WORK_FIELDS must NOT contain 'genre' — genre is episode-level.

    All three routing sites (sync, routers/episodes, views) import this
    constant, so one correct value fixes all three.
    """
    assert "genre" not in WORK_FIELDS, (
        "genre must NOT be in WORK_FIELDS; it is episode-level, not work-level. "
        "Routing genre to the work entity makes MANUAL genre edits invisible to sync."
    )
    assert "author" in WORK_FIELDS, "author must be in WORK_FIELDS (work-level)"
    assert "year" in WORK_FIELDS, "year must be in WORK_FIELDS (work-level)"


# ---------------------------------------------------------------------------
# Test 2: MANUAL genre → compute_resolved → sync_episode_tags rewrite diff
# ---------------------------------------------------------------------------


def test_genre_manual_edit_flows_to_sync(db_session, tmp_path):
    """PATCH genre (MANUAL, episode entity) → compute_resolved returns it
    → sync_episode_tags produces a 'rewrite' diff carrying 'Fantasy'.

    This is the critical cross-component check: if genre were still routed to
    'work' (the old sync.WORK_FIELDS bug), the MetadataValue would be stored
    on the wrong entity, compute_resolved would find nothing, and the sync
    diff would show resolved_value='' instead of 'Fantasy'.
    """
    ep, _wk = _setup_episode(db_session)

    # Simulate PATCH /api/v1/episodes/{id}/metadata field=genre value=Fantasy
    record_value(
        db_session,
        entity_type="episode",
        entity_id=ep.id,
        field="genre",
        value="Fantasy",
        origin=FieldOrigin.MANUAL,
        source="user",
    )
    db_session.flush()

    # compute_resolved must reflect the MANUAL genre from the episode entity
    resolved = compute_resolved(db_session, ep)
    assert resolved["genre"] == "Fantasy", (
        f"compute_resolved returned genre={resolved['genre']!r}, expected 'Fantasy'. "
        "If genre is routed to 'work', no MetadataValue exists there → empty fallback."
    )

    # sync_episode_tags must produce a rewrite diff for genre
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 412)

    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=str(audio),
    )
    db_session.add(asset)
    db_session.flush()

    # Stub read_tags to return no genre (empty file), write_tags to no-op
    with (
        patch("audiobiblio.library.sync.read_tags", return_value={}),
        patch("audiobiblio.library.sync.write_tags"),
    ):
        report = sync_episode_tags(db_session, ep, write=False)

    genre_diff = next((d for d in report.diffs if d.field == "genre"), None)
    assert genre_diff is not None, (
        "Expected a 'genre' FieldDiff in the sync report; none found. "
        f"All diff fields: {[d.field for d in report.diffs]}"
    )
    assert genre_diff.resolved_value == "Fantasy", (
        f"Sync genre diff has resolved_value={genre_diff.resolved_value!r}, expected 'Fantasy'."
    )
    assert genre_diff.action == "rewrite", (
        f"Expected genre diff action='rewrite' (file has no genre tag), got {genre_diff.action!r}."
    )


# ---------------------------------------------------------------------------
# Test 3: author is WORK-routed (counter-case)
# ---------------------------------------------------------------------------


def test_author_routes_to_work_entity(db_session):
    """Author is work-level. A MANUAL author MetadataValue on the Work entity
    is returned by compute_resolved. This is the counter-case to genre: it
    confirms work-routing works correctly for the fields that should use it.
    """
    ep, wk = _setup_episode(db_session)

    # Record MANUAL author on the WORK entity (correct routing for author)
    record_value(
        db_session,
        entity_type="work",
        entity_id=wk.id,
        field="author",
        value="Tolkien",
        origin=FieldOrigin.MANUAL,
        source="user",
    )
    db_session.flush()

    resolved = compute_resolved(db_session, ep)
    assert resolved["author"] == "Tolkien", (
        f"compute_resolved returned author={resolved['author']!r}, expected 'Tolkien'. "
        "Author is work-routed; the MetadataValue must be on entity_type='work'."
    )
