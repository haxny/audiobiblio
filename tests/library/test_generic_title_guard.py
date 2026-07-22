"""
Tests for the generic-title guard (Phase 4, Task 2).

"Epizody pořadu" (and other placeholder titles returned by mujrozhlas)
must NEVER become an episode title, a filename stem, or an audio tag.

Strategy
--------
* Unit-test ``is_generic_title`` directly (normalisation, diacritics).
* Integration-test the ingest guard via ``upsert_from_item`` with a real
  in-memory SQLite session — same fixture pattern used by other tests.
* Unit-test the filename guard via ``build_paths_for_episode`` with a
  plain mock Episode.
* Unit-test the tag guard via ``tag_audio`` — reuse the silent_m4a
  fixture and check the written title tag is absent.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest
from mutagen.mp4 import MP4

from audiobiblio.core.db.models import Episode, Work, Station, Program, Series
from audiobiblio.dedupe.matching import GENERIC_TITLES, is_generic_title
from audiobiblio.library.pipelines.library import build_paths_for_episode
from audiobiblio.library.pipelines.postprocess import tag_audio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hierarchy(db_session, *, program_name: str = "GenGuardProg"):
    """Create Station → Program → Series → Work chain; return (work,)."""
    station = db_session.query(Station).filter_by(code="gg").one_or_none()
    if station is None:
        station = Station(code="gg", name="Guard Station")
        db_session.add(station)
        db_session.flush()

    program = db_session.query(Program).filter_by(name=program_name).one_or_none()
    if program is None:
        program = Program(station_id=station.id, name=program_name)
        db_session.add(program)
        db_session.flush()

    series = db_session.query(Series).filter_by(
        program_id=program.id, name=f"{program_name} S"
    ).one_or_none()
    if series is None:
        series = Series(program_id=program.id, name=f"{program_name} S")
        db_session.add(series)
        db_session.flush()

    work = Work(series_id=series.id, title="Test Work", author="Autor Testovaci")
    db_session.add(work)
    db_session.flush()
    return work


# ---------------------------------------------------------------------------
# is_generic_title unit tests
# ---------------------------------------------------------------------------


class TestIsGenericTitle:
    """``is_generic_title`` must normalise (diacritics + case + whitespace)."""

    def test_returns_false_for_empty_string(self):
        assert is_generic_title("") is False

    def test_returns_false_for_none_like_empty(self):
        # The function takes str; callers guard with `if title`, but a blank
        # string should still be False (not generic — it's absent).
        assert is_generic_title("  ") is False

    def test_epizody_poradu_ascii(self):
        """Normalised ASCII form is in GENERIC_TITLES."""
        assert is_generic_title("epizody poradu") is True

    def test_epizody_poradu_with_diacritics(self):
        """Real mujrozhlas leak: 'Epizody pořadu' (diacritics + title-case)."""
        assert is_generic_title("Epizody pořadu") is True

    def test_epizody_poradu_upper(self):
        assert is_generic_title("EPIZODY PORADU") is True

    def test_episodes_english(self):
        assert is_generic_title("episodes") is True

    def test_episodes_with_leading_whitespace(self):
        assert is_generic_title("  episodes  ") is True

    def test_normal_title_false(self):
        assert is_generic_title("Kapitola 1") is False

    def test_normal_title_with_diacritics_false(self):
        assert is_generic_title("Příběh jednoho muže") is False

    def test_generic_titles_set_is_public(self):
        """GENERIC_TITLES must be publicly accessible and include known entries."""
        assert "epizody poradu" in GENERIC_TITLES
        assert "episodes" in GENERIC_TITLES


# ---------------------------------------------------------------------------
# Ingest guard — upsert_from_item
# ---------------------------------------------------------------------------


class TestIngestGuard:
    """Generic title at ingest must be replaced by 'Episode N' fallback."""

    def test_generic_title_replaced_on_new_episode(self, db_session):
        """'Epizody pořadu' (with diacritics) must not become the episode title."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        ep, _work = upsert_from_item(
            db_session,
            url="https://mujrozhlas.cz/ep/1",
            item_title="Epizody pořadu",
            series_name="Test Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=5,
            ext_id="ep-gen-001",
        )
        assert ep.title != "Epizody pořadu"
        assert ep.title == "Episode 5"

    def test_generic_title_ascii_replaced(self, db_session):
        """'epizody poradu' (ASCII variant) is also a generic title."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        ep, _work = upsert_from_item(
            db_session,
            url="https://mujrozhlas.cz/ep/2",
            item_title="epizody poradu",
            series_name="Test Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=7,
            ext_id="ep-gen-002",
        )
        assert ep.title == "Episode 7"

    def test_normal_title_preserved(self, db_session):
        """Non-generic titles must pass through unchanged."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        ep, _work = upsert_from_item(
            db_session,
            url="https://mujrozhlas.cz/ep/3",
            item_title="Kapitola první",
            series_name="Test Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=1,
            ext_id="ep-norm-001",
        )
        assert ep.title == "Kapitola prvni"  # unidecoded at ingest

    def test_generic_title_does_not_overwrite_good_title_on_reingest(self, db_session):
        """Re-ingesting with a generic title must not overwrite the existing good title."""
        from audiobiblio.library.pipelines.ingest import upsert_from_item

        # First ingest: good title
        ep_first, _work = upsert_from_item(
            db_session,
            url="https://mujrozhlas.cz/ep/reingest",
            item_title="Skutečný název epizody",
            series_name="Reingest Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=3,
            ext_id="ep-reingest-001",
        )
        assert ep_first.title == "Skutecny nazev epizody"  # unidecoded at ingest
        first_id = ep_first.id

        # Second ingest same ext_id with generic title → title must be preserved
        ep_second, _work2 = upsert_from_item(
            db_session,
            url="https://mujrozhlas.cz/ep/reingest",
            item_title="Epizody pořadu",
            series_name="Reingest Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=3,
            ext_id="ep-reingest-001",
        )
        assert ep_second.id == first_id
        assert ep_second.title == "Skutecny nazev epizody"  # unidecoded at ingest


# ---------------------------------------------------------------------------
# Filename / path guard
# ---------------------------------------------------------------------------


class TestFilenameGuard:
    """build_paths_for_episode must treat generic ep titles as absent."""

    def _make_ep(self, title: str) -> object:
        """Minimal stand-in with episode attributes (no DB needed)."""
        class _FakeEp:
            work = None
        ep = _FakeEp()
        ep.title = title
        ep.episode_number = 4
        ep.published_at = None
        return ep

    def test_generic_title_absent_from_stem(self):
        stem = build_paths_for_episode(self._make_ep("epizody poradu"))["stem"]
        assert "epizody" not in stem.lower()
        assert "poradu" not in stem.lower()

    def test_generic_title_with_diacritics_absent_from_stem(self):
        stem = build_paths_for_episode(self._make_ep("Epizody pořadu"))["stem"]
        assert "epizody" not in stem.lower()
        assert "poradu" not in stem.lower()

    def test_normal_title_present_in_stem(self):
        stem = build_paths_for_episode(self._make_ep("Krasna Kapitola"))["stem"]
        assert "Krasna" in stem or "krasna" in stem.lower()


# ---------------------------------------------------------------------------
# Tag guard
# ---------------------------------------------------------------------------


class TestTagGuard:
    """tag_audio must not write a generic title into the title tag."""

    def _make_ep_work(self, db_session, title: str) -> tuple[Episode, Work]:
        from audiobiblio.core.db.models import Station, Program, Series

        station = db_session.query(Station).filter_by(code="tg2").one_or_none()
        if station is None:
            station = Station(code="tg2", name="Tag Guard Station")
            db_session.add(station)
            db_session.flush()

        program = db_session.query(Program).filter_by(name="TagGuardProg").one_or_none()
        if program is None:
            program = Program(station_id=station.id, name="TagGuardProg")
            db_session.add(program)
            db_session.flush()

        series = db_session.query(Series).filter_by(
            program_id=program.id, name="TagGuardProg S"
        ).one_or_none()
        if series is None:
            series = Series(program_id=program.id, name="TagGuardProg S")
            db_session.add(series)
            db_session.flush()

        work = Work(series_id=series.id, title="Test Work Tag", author="Autor")
        db_session.add(work)
        db_session.flush()

        ep = Episode(
            work_id=work.id,
            title=title,
            ext_id=f"ext-tag-guard-{id(work)}",
            url="https://example.cz/ep-tag",
            episode_number=3,
        )
        db_session.add(ep)
        db_session.flush()
        return ep, work

    def test_generic_title_not_written_as_track_title(self, db_session, silent_m4a: Path):
        """If ep.title is a generic placeholder, track title tag must be empty."""
        ep, work = self._make_ep_work(db_session, "epizody poradu")
        tag_audio(silent_m4a, ep, work, force=True)
        tags = MP4(str(silent_m4a))
        # title tag: ©nam; if absent or empty, the guard worked
        title_tag = tags.get("\xa9nam", [""])
        written = title_tag[0] if title_tag else ""
        assert written == ""

    def test_normal_title_written_as_track_title(self, db_session, silent_m4a: Path):
        """Normal ep titles that differ from the album ARE written as track title."""
        ep, work = self._make_ep_work(db_session, "Specialni Epizoda")
        tag_audio(silent_m4a, ep, work, force=True)
        tags = MP4(str(silent_m4a))
        title_tag = tags.get("\xa9nam", [""])
        written = title_tag[0] if title_tag else ""
        # "Specialni Epizoda" unidecoded differs from album "Test Work Tag"
        assert "Specialni" in written or "specialni" in written.lower()
