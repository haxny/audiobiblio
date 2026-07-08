"""
Tests for the segmentation engine (Phase 6, Task 1) — pure analysis.

Fixtures are verbatim patterns from the task brief:
- Wassermann/Kolumbus serialized: 2 parts → 1 ProposedWork, 2 episode IDs
- Horký + Svoboda anthology: author-prefix, no parts → 2 per-episode ProposedWorks
- SFT documentary sentences: no colon → magazine (no false-positive author detection)
- Generic/fallback titles ("Episode 3", "Epizody pořadu") → unassigned
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Base,
    Episode,
    Program,
    Series,
    Station,
    Work,
)
from audiobiblio.library.segmentation import (
    ProposedWork,
    SegmentationProposal,
    propose_segmentation,
)


# ---------------------------------------------------------------------------
# Shared DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = factory()
    yield s
    s.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Helper: build a minimal Station → Program → Series → Work → Episode chain
# ---------------------------------------------------------------------------


def _make_program(session, name: str = "Test Program") -> Program:
    station = Station(code=f"s{abs(hash(name)) % 9999}", name=f"Station {name}")
    session.add(station)
    session.flush()
    program = Program(station_id=station.id, name=name)
    session.add(program)
    session.flush()
    return program


def _add_episodes(
    session,
    program: Program,
    titles: list[str],
    series_name: str | None = None,
) -> list[Episode]:
    """Add a list of episodes, each under its own Work, all under one Series."""
    if series_name is None:
        series_name = f"Series for {program.name}"
    series = Series(program_id=program.id, name=series_name)
    session.add(series)
    session.flush()

    episodes: list[Episode] = []
    for i, title in enumerate(titles, start=1):
        work = Work(series_id=series.id, title=f"Catchall {program.id}-{i}")
        session.add(work)
        session.flush()
        ep = Episode(
            work_id=work.id,
            title=title,
            ext_id=f"seg-{program.id}-{i}",
            url=f"https://example.cz/ep-{program.id}-{i}",
            episode_number=i,
            published_at=datetime(2024, 1, i),
        )
        session.add(ep)
        session.flush()
        episodes.append(ep)
    return episodes


# ---------------------------------------------------------------------------
# 1. Wassermann / Kolumbus — serialized (2 parts → 1 ProposedWork, 2 eps)
# ---------------------------------------------------------------------------


class TestWassermannSerialized:
    """Author-prefix + part markers → one ProposedWork per book, 2 episodes."""

    def test_mode_is_serialized(self, session):
        program = _make_program(session, "Wassermann Prog")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "serialized"

    def test_one_proposed_work(self, session):
        program = _make_program(session, "Wassermann Prog2")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 1

    def test_proposed_work_has_both_episode_ids(self, session):
        program = _make_program(session, "Wassermann Prog3")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert set(pw.episode_ids) == {eps[0].id, eps[1].id}

    def test_proposed_work_title_is_book_key(self, session):
        program = _make_program(session, "Wassermann Prog4")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert pw.title == "Kolumbus"

    def test_proposed_work_author(self, session):
        program = _make_program(session, "Wassermann Prog5")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert pw.author == "Jakub Wassermann"

    def test_proposed_work_signal(self, session):
        program = _make_program(session, "Wassermann Prog6")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert pw.signal == "author_title_parts"

    def test_proposed_work_confidence_is_1_0(self, session):
        program = _make_program(session, "Wassermann Prog7")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert pw.confidence == 1.0

    def test_no_unassigned(self, session):
        program = _make_program(session, "Wassermann Prog8")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        result = propose_segmentation(session, program)
        assert len(result.unassigned) == 0

    def test_dash_part_markers_also_serialized(self, session):
        """'Author: Title - 1' / '- 2' pattern also clusters."""
        program = _make_program(session, "Wassermann DashProg")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus - 1",
                "Jakub Wassermann: Kolumbus - 2",
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "serialized"
        assert len(result.proposed) == 1
        assert set(result.proposed[0].episode_ids) == {eps[0].id, eps[1].id}


# ---------------------------------------------------------------------------
# 2. Horký + Svoboda — anthology (author-prefix, no parts, per-episode works)
# ---------------------------------------------------------------------------


class TestAnthologyStories:
    """Author-prefix without part markers → one ProposedWork PER EPISODE."""

    def test_mode_is_anthology(self, session):
        program = _make_program(session, "Anthology Prog")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "anthology"

    def test_two_proposed_works(self, session):
        program = _make_program(session, "Anthology Prog2")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 2

    def test_each_work_has_one_episode(self, session):
        program = _make_program(session, "Anthology Prog3")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert len(pw.episode_ids) == 1

    def test_authors_are_extracted(self, session):
        program = _make_program(session, "Anthology Prog4")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        authors = {pw.author for pw in result.proposed}
        assert "Karel Horký" in authors
        assert "Ondřej Svoboda" in authors

    def test_titles_are_rest_part(self, session):
        program = _make_program(session, "Anthology Prog5")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        titles = {pw.title for pw in result.proposed}
        assert "Příběh první" in titles
        assert "Jiný příběh" in titles

    def test_signal_is_author_title(self, session):
        program = _make_program(session, "Anthology Prog6")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert pw.signal == "author_title"

    def test_confidence_is_0_9(self, session):
        program = _make_program(session, "Anthology Prog7")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert pw.confidence == 0.9


# ---------------------------------------------------------------------------
# 3. SFT documentary sentences — magazine (period separator, NOT colon)
# ---------------------------------------------------------------------------


class TestSFTDocumentaryMagazine:
    """Titles with periods (not colons) must NOT trigger author detection."""

    def test_mode_is_magazine(self, session):
        program = _make_program(session, "SFT Prog")
        _add_episodes(
            session,
            program,
            [
                "Zlatý poklad republiky. Kam zmizely státní rezervy?",
                "Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?",
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "magazine"

    def test_no_author_extracted(self, session):
        program = _make_program(session, "SFT Prog2")
        _add_episodes(
            session,
            program,
            [
                "Zlatý poklad republiky. Kam zmizely státní rezervy?",
                "Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert pw.author is None

    def test_two_proposed_works_one_per_episode(self, session):
        program = _make_program(session, "SFT Prog3")
        _add_episodes(
            session,
            program,
            [
                "Zlatý poklad republiky. Kam zmizely státní rezervy?",
                "Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?",
            ],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 2
        for pw in result.proposed:
            assert len(pw.episode_ids) == 1

    def test_signal_is_episode_title(self, session):
        program = _make_program(session, "SFT Prog4")
        _add_episodes(
            session,
            program,
            [
                "Zlatý poklad republiky. Kam zmizely státní rezervy?",
                "Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert pw.signal == "episode_title"

    def test_confidence_is_0_7(self, session):
        program = _make_program(session, "SFT Prog5")
        _add_episodes(
            session,
            program,
            [
                "Zlatý poklad republiky. Kam zmizely státní rezervy?",
                "Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?",
            ],
        )
        result = propose_segmentation(session, program)
        for pw in result.proposed:
            assert pw.confidence == 0.7

    def test_false_positive_guard_zlatý_poklad(self, session):
        """'Zlatý poklad republiky. Kam zmizely…' must NOT be parsed as author:title."""
        program = _make_program(session, "SFT FalsePositive")
        _add_episodes(
            session,
            program,
            ["Zlatý poklad republiky. Kam zmizely státní rezervy?"],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 1
        pw = result.proposed[0]
        assert pw.author is None
        assert pw.signal == "episode_title"

    def test_false_positive_guard_nikola_šuhaj(self, session):
        """'Stalo se v zemi Nikoly Šuhaje…' must NOT be parsed as author:title."""
        program = _make_program(session, "SFT FalsePositive2")
        _add_episodes(
            session,
            program,
            ["Stalo se v zemi Nikoly Šuhaje. Jak to bylo doopravdy?"],
        )
        result = propose_segmentation(session, program)
        pw = result.proposed[0]
        assert pw.author is None


# ---------------------------------------------------------------------------
# 4. Generic / fallback titles → unassigned
# ---------------------------------------------------------------------------


class TestGenericTitlesUnassigned:
    """Episodes with generic or 'Episode N' titles go to unassigned."""

    def test_episode_n_pattern_unassigned(self, session):
        program = _make_program(session, "Generic Prog")
        eps = _add_episodes(
            session,
            program,
            ["Episode 3"],
        )
        result = propose_segmentation(session, program)
        assert eps[0].id in result.unassigned
        all_ep_ids = {eid for pw in result.proposed for eid in pw.episode_ids}
        assert eps[0].id not in all_ep_ids

    def test_generic_mujrozhlas_title_unassigned(self, session):
        program = _make_program(session, "Generic Prog2")
        eps = _add_episodes(
            session,
            program,
            ["Epizody pořadu"],
        )
        result = propose_segmentation(session, program)
        assert eps[0].id in result.unassigned
        all_ep_ids = {eid for pw in result.proposed for eid in pw.episode_ids}
        assert eps[0].id not in all_ep_ids

    def test_generic_episode_not_proposed(self, session):
        """Mix: 2 real serialized + 1 generic → generic in unassigned only."""
        program = _make_program(session, "Mixed Generic")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
                "Episode 3",
            ],
        )
        result = propose_segmentation(session, program)
        assert eps[2].id in result.unassigned
        assert len(result.proposed) == 1
        assert set(result.proposed[0].episode_ids) == {eps[0].id, eps[1].id}


# ---------------------------------------------------------------------------
# 5. Mixed-program case
# ---------------------------------------------------------------------------


class TestMixedProgram:
    """A program can have episodes of different signals; mode = majority."""

    def test_majority_determines_mode(self, session):
        """3 serialized-signal + 1 anthology-signal → mode is serialized."""
        program = _make_program(session, "Mixed Mode Prog")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/3)",
                "Jakub Wassermann: Kolumbus (2/3)",
                "Jakub Wassermann: Kolumbus (3/3)",
                "Karel Horký: Příběh o ničem",  # anthology signal
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "serialized"

    def test_anthology_majority(self, session):
        """3 anthology-signal + 1 episode_title → mode is anthology."""
        program = _make_program(session, "Mixed Anthology Prog")
        _add_episodes(
            session,
            program,
            [
                "Karel Horký: Příběh první",
                "Ondřej Svoboda: Jiný příběh",
                "Jan Neruda: Malostranské povídky",
                "Zlatý poklad republiky bez autora",  # episode_title (no colon)
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "anthology"


# ---------------------------------------------------------------------------
# 6. Dataclass structure
# ---------------------------------------------------------------------------


class TestDataclassStructure:
    """Ensure ProposedWork and SegmentationProposal have correct structure."""

    def test_proposed_work_is_frozen(self):
        pw = ProposedWork(
            title="Test",
            author="Author",
            episode_ids=(1, 2),
            signal="author_title_parts",
            confidence=1.0,
        )
        with pytest.raises((AttributeError, TypeError)):
            pw.title = "Changed"  # type: ignore[misc]

    def test_segmentation_proposal_has_correct_fields(self, session):
        program = _make_program(session, "Structure Prog")
        _add_episodes(session, program, ["Karel Horký: Příběh první"])
        result = propose_segmentation(session, program)
        assert isinstance(result, SegmentationProposal)
        assert hasattr(result, "program_id")
        assert hasattr(result, "mode")
        assert hasattr(result, "proposed")
        assert hasattr(result, "unassigned")
        assert hasattr(result, "note")
        assert result.program_id == program.id

    def test_proposed_is_tuple(self, session):
        program = _make_program(session, "Tuple Prog")
        _add_episodes(session, program, ["Karel Horký: Příběh první"])
        result = propose_segmentation(session, program)
        assert isinstance(result.proposed, tuple)

    def test_unassigned_is_tuple(self, session):
        program = _make_program(session, "Unassigned Tuple Prog")
        _add_episodes(session, program, ["Karel Horký: Příběh první"])
        result = propose_segmentation(session, program)
        assert isinstance(result.unassigned, tuple)

    def test_empty_program_returns_magazine(self, session):
        """No episodes → magazine with empty proposed and unassigned."""
        program = _make_program(session, "Empty Prog")
        result = propose_segmentation(session, program)
        assert result.mode == "magazine"
        assert result.proposed == ()
        assert result.unassigned == ()
