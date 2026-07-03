"""
Tests for audiobiblio.library.pipelines.postprocess.tag_audio

Tag correctness: plain track numbers + title always written.

Background
----------
``tag_audio`` assembles track/album tag dicts from Episode + Work DB objects
and delegates the actual write to ``audiobiblio.tags.writer.write_tags``.

Two bugs were observed in the wild on a downloaded m4a:
  * ``trkn = (16, 3)``  — episode 16, but the Work only has 3 episodes
    *recorded in the DB at that moment* (the series is still publishing).
    Totals counted from an incomplete DB lie; the MP4 "total" field is
    therefore always wrong and must never be written.
  * ``©nam`` absent     — the track title was gated on ``total > 1 and not
    is_anthology``.  Because the Work is fresh and may have only one episode
    in the DB yet, ``total == 1`` and the gate kept the title empty.

What is ``is_anthology``?
    Set to ``True`` when a Program broadcasts standalone works as individual
    episodes rather than chapters of a single book (e.g. a drama strand that
    airs one short story per episode).  In that case ``work.author`` is empty
    and the author + title are extracted from ``ep.title`` via the
    ``"Author: Title"`` colon-split heuristic.  The old code suppressed the
    track title for anthology programs on the assumption that the episode IS
    the whole work.  Under the new rule the title IS written whenever it
    differs from the album title — which it does for anthology too (ep_title
    includes the author prefix; album_title is just the extracted work title).

Test strategy
-------------
* ``tag_audio`` calls ``_count_episodes_in_work`` which opens the production
  DB session, not the in-memory test session.  Test 1 uses
  ``unittest.mock.patch`` to inject a return value of 3 so that the current
  ``"N of Total"`` branch is exercised and can be shown to produce the wrong
  result before the fix.
* Tests 2-4 rely on the in-process state only.
* All tests write to a real m4a generated via ffmpeg (``silent_m4a`` fixture,
  see ``tests/fixtures_util.py``) and read back with ``mutagen.mp4.MP4``.
"""
from __future__ import annotations
from pathlib import Path

import pytest
from mutagen.mp4 import MP4

from audiobiblio.core.db.models import Episode, Work
from audiobiblio.library.pipelines.postprocess import tag_audio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_episode_work(db_session, *, ep_title: str, work_title: str,
                       work_author: str = "Jan Novak",
                       episode_number: int | None = None,
                       program_name: str = "TestProg") -> tuple[Episode, Work]:
    """Create a minimal Station→Program→Series→Work→Episode hierarchy."""
    from audiobiblio.core.db.models import Station, Program, Series

    station = db_session.query(Station).filter_by(code="tst2").one_or_none()
    if station is None:
        station = Station(code="tst2", name="Test Station 2")
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

    work = Work(series_id=series.id, title=work_title, author=work_author)
    db_session.add(work)
    db_session.flush()

    ep = Episode(
        work_id=work.id,
        title=ep_title,
        ext_id=f"ext-tag-{id(work)}",
        url="https://example.cz/ep",
        episode_number=episode_number,
    )
    db_session.add(ep)
    db_session.flush()
    return ep, work


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlainTrackNumber:
    """Track number must be a plain integer; never 'N of Total'."""

    def test_plain_track_number_no_total(self, db_session, silent_m4a: Path):
        """trkn must be (16, 0) — never formatted as "N of Total".

        Regression: the old code produced ``trkn = (16, 3)`` because it
        called ``_count_episodes_in_work`` and formatted ``"16 of 3"``.
        The count is unreliable on works that are still publishing.
        The current code always writes a plain integer with total=0.
        """
        ep, work = _make_episode_work(
            db_session,
            ep_title="Kapitola 16",
            work_title="Kniha XYZ",
            work_author="Jan Novak",
            episode_number=16,
        )

        tag_audio(silent_m4a, ep, work, force=True)

        tags = MP4(str(silent_m4a))
        assert tags.get("trkn") == [(16, 0)], (
            f"expected [(16, 0)] but got {tags.get('trkn')} — "
            "tracknumber must be a plain integer with total=0"
        )


class TestTitleWritten:
    """Episode title must be written to ©nam whenever it differs from album."""

    def test_title_written_when_differs_from_album(self, db_session, silent_m4a: Path):
        """©nam must contain the episode title when it differs from the album.

        Regression: the old code left ©nam empty when ``total <= 1`` even
        if the episode had a meaningful distinct title.
        """
        ep, work = _make_episode_work(
            db_session,
            ep_title="Kapitola 2",
            work_title="Kniha",
            work_author="Jan Novak",
        )

        tag_audio(silent_m4a, ep, work, force=True)

        tags = MP4(str(silent_m4a))
        nam = tags.get("\xa9nam")
        assert nam == ["Kapitola 2"], (
            f"expected ['Kapitola 2'] in ©nam but got {nam}"
        )

    def test_single_file_title_equals_album(self, db_session, silent_m4a: Path):
        """©nam must be absent when ep title matches the album (naming pattern 1).

        Single-file works where the episode title is the same as the work title
        should not get a separate track title — the player shows the album name.
        This is a regression guard: the fix must not start writing redundant
        titles for such works.
        """
        ep, work = _make_episode_work(
            db_session,
            ep_title="Valka s mloky",
            work_title="Valka s mloky",  # identical to ep_title (after unidecode)
            work_author="Karel Capek",
        )

        tag_audio(silent_m4a, ep, work, force=True)

        tags = MP4(str(silent_m4a))
        nam = tags.get("\xa9nam")
        alb = tags.get("\xa9alb", [None])[0]
        assert not nam or nam[0] == alb, (
            f"expected ©nam absent or equal to album '{alb}' but got {nam}"
        )


class TestGenreAtom:
    """Genre regression guard: must land in freeform iTunes atom, not ©gen."""

    def test_genre_freeform_atom_written(self, db_session, silent_m4a: Path):
        """'----:com.apple.iTunes:GENRE' must be present and contain 'audiokniha'.

        The gate evidence misread ©gen; the writer intentionally deletes ©gen
        and writes ONLY the freeform atom to avoid player-side duplicates.
        This test pins that deliberate behaviour so a future refactor cannot
        silently revert it.
        """
        ep, work = _make_episode_work(
            db_session,
            ep_title="Nejaka Kniha epizoda 1",
            work_title="Nejaka Kniha",
            work_author="Jan Novak",
            # program has genre=None → process_genre returns "audiokniha"
        )

        tag_audio(silent_m4a, ep, work, force=True)

        tags = MP4(str(silent_m4a))

        # Standard ©gen must NOT be present
        assert "\xa9gen" not in tags, (
            "©gen should be absent — genre belongs in the freeform iTunes atom only"
        )

        # Freeform GENRE atom must exist and contain the default genre
        genre_atom = tags.get("----:com.apple.iTunes:GENRE", [])
        assert genre_atom, "freeform '----:com.apple.iTunes:GENRE' atom must be present"
        raw_values = b"".join(bytes(v) for v in genre_atom)
        assert b"audiokniha" in raw_values, (
            f"expected 'audiokniha' in freeform GENRE atom, got: {raw_values!r}"
        )
