"""
Tests for the segmentation engine (Phase 6, Tasks 1 & 2).

Task 1 — pure analysis (propose_segmentation):
- Wassermann/Kolumbus serialized: 2 parts → 1 ProposedWork, 2 episode IDs
- Horký + Svoboda anthology: author-prefix, no parts → 2 per-episode ProposedWorks
- SFT documentary sentences: no colon → magazine (no false-positive author detection)
- Generic/fallback titles ("Episode 3", "Epizody pořadu") → unassigned

Task 2 — apply (apply_segmentation):
- Re-parent moves episodes; children (Assets) untouched
- Find-or-create idempotence (re-apply → no dupes, actions say "already")
- Empty-work deletion rules (MANUAL row blocks deletion)
- expected_total note emitted
- Dry-run purity (no session mutations)
- only_titles filter
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Asset,
    AssetType,
    Base,
    Episode,
    FieldOrigin,
    MetadataValue,
    Program,
    Series,
    Station,
    Work,
)
from audiobiblio.library.segmentation import (
    ProposedWork,
    SegmentationProposal,
    apply_segmentation,
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
        _add_episodes(session, program, ["Karel Horský: Příběh první"])
        result = propose_segmentation(session, program)
        assert isinstance(result.unassigned, tuple)

    def test_empty_program_returns_magazine(self, session):
        """No episodes → magazine with empty proposed and unassigned."""
        program = _make_program(session, "Empty Prog")
        result = propose_segmentation(session, program)
        assert result.mode == "magazine"
        assert result.proposed == ()
        assert result.unassigned == ()


# ---------------------------------------------------------------------------
# 7. Edge cases for coverage fixes (Phase 6 coverage pins)
# ---------------------------------------------------------------------------


class TestDilPartMarker:
    """(I1) N. díl / N. část pattern: serialized clustering with correct part order."""

    def test_dil_marker_creates_serialized_cluster(self, session):
        """'Jakub Wassermann: Kolumbus 1. díl' + '2. díl' → 1 serialized work, 2 episodes."""
        program = _make_program(session, "Wassermann Dil Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus 1. díl",
                "Jakub Wassermann: Kolumbus 2. díl",
            ],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "serialized"
        assert len(result.proposed) == 1
        pw = result.proposed[0]
        assert pw.title == "Kolumbus"
        assert pw.author == "Jakub Wassermann"
        assert set(pw.episode_ids) == {eps[0].id, eps[1].id}
        assert pw.signal == "author_title_parts"

    def test_dil_marker_preserves_part_order(self, session):
        """Episodes with 1. díl, 2. díl, 3. díl must be ordered by part number."""
        program = _make_program(session, "Wassermann Dil Order")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Příběh 3. díl",
                "Jakub Wassermann: Příběh 1. díl",
                "Jakub Wassermann: Příběh 2. díl",
            ],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 1
        pw = result.proposed[0]
        # Episodes should be ordered by part number (1, 2, 3), not published order
        assert pw.episode_ids == (eps[1].id, eps[2].id, eps[0].id)


class TestFourWordPrefixFalsePositive:
    """(I2) 4-word boundary false-positive: document current behavior with comment."""

    def test_four_word_prefix_known_false_positive(self, session):
        """
        Known limitation: "Velká hra jasnovidce Hanussena: kapitola první" is accepted
        as an author-prefix pattern because "Velká hra jasnovidce Hanussena" is exactly
        4 words and passes the _looks_like_name guard (≤4 words, no digits).

        This is likely a false positive (not a real person name), but currently the
        segmentation engine accepts it. Document this as a regression baseline so
        future tightening of the 4-word guard can verify it doesn't regress.
        """
        program = _make_program(session, "Hanussen FalsePositive")
        _add_episodes(
            session,
            program,
            ["Velká hra jasnovidce Hanussena: kapitola první"],
        )
        result = propose_segmentation(session, program)
        assert len(result.proposed) == 1
        pw = result.proposed[0]
        # Currently accepted as author prefix despite being unlikely to be a real name
        assert pw.author == "Velká hra jasnovidce Hanussena"
        assert pw.signal == "author_title"  # No part marker, so anthology


class TestThreeWordAuthor:
    """(M2) 3-word author name: recognized as valid, creates anthology work."""

    def test_three_word_author_anthology(self, session):
        """František Xaver Svoboda (3 words) is valid author → anthology work."""
        program = _make_program(session, "Svoboda Prog")
        eps = _add_episodes(
            session,
            program,
            ["František Xaver Svoboda: Zázračný bič"],
        )
        result = propose_segmentation(session, program)
        assert result.mode == "anthology"
        assert len(result.proposed) == 1
        pw = result.proposed[0]
        assert pw.author == "František Xaver Svoboda"
        assert pw.title == "Zázračný bič"
        assert pw.episode_ids == (eps[0].id,)
        assert pw.signal == "author_title"
        assert pw.confidence == 0.9


class TestClusterKeyWhitespaceNormalization:
    """(M4) cluster-key sensitivity: whitespace in book_key normalizes or documents split."""

    def test_cluster_key_trailing_whitespace_normalizes(self, session):
        """Kolumbus (1/2) vs Kolumbus  (2/2) [double space] → normalize to 1 cluster."""
        program = _make_program(session, "Whitespace Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus  (2/2)",  # double space before (2/2)
            ],
        )
        result = propose_segmentation(session, program)
        # If whitespace is properly normalized via .strip(), we get 1 cluster
        assert len(result.proposed) == 1, (
            "Expected 1 cluster (whitespace should normalize), "
            f"but got {len(result.proposed)}. "
            "If this fails, book_key normalization may need collapsing internal spaces."
        )
        pw = result.proposed[0]
        assert set(pw.episode_ids) == {eps[0].id, eps[1].id}
        assert pw.title == "Kolumbus"


# ---------------------------------------------------------------------------
# 8. apply_segmentation — Task 2
# ---------------------------------------------------------------------------


class TestApplySegmentation:
    """Tests for apply_segmentation: re-parenting, provenance, cleanup, dry-run."""

    # --- 8.1 Re-parent moves episodes ---

    def test_reparent_moves_episodes(self, session):
        """After apply, all episodes in a ProposedWork have the new work's work_id."""
        program = _make_program(session, "Apply Basic Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        old_work_ids = {ep.work_id for ep in eps}
        proposal = propose_segmentation(session, program)

        apply_segmentation(session, proposal, dry_run=False)

        # Refresh from DB
        for ep in eps:
            session.refresh(ep)
        new_work_ids = {ep.work_id for ep in eps}
        # All episodes share a single new work
        assert len(new_work_ids) == 1
        # The new work is different from any old catch-all work
        assert new_work_ids.isdisjoint(old_work_ids)

    # --- 8.2 Children untouched (Asset still reachable via episode) ---

    def test_children_untouched(self, session):
        """Re-parenting an episode does not affect its Assets."""
        program = _make_program(session, "Children Untouched Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        # Add an Asset to the first episode
        asset = Asset(episode_id=eps[0].id, type=AssetType.AUDIO)
        session.add(asset)
        session.flush()
        asset_id = asset.id

        proposal = propose_segmentation(session, program)
        apply_segmentation(session, proposal, dry_run=False)

        # Asset still exists and is attached to the same episode
        session.expire_all()
        found = session.get(Asset, asset_id)
        assert found is not None
        assert found.episode_id == eps[0].id

    # --- 8.3 Find-or-create idempotence ---

    def test_find_or_create_idempotence(self, session):
        """Re-applying the same proposal creates no duplicate works."""
        program = _make_program(session, "Idempotent Works Prog")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        proposal = propose_segmentation(session, program)

        apply_segmentation(session, proposal, dry_run=False)
        work_count_after_first = session.query(Work).count()

        apply_segmentation(session, proposal, dry_run=False)
        work_count_after_second = session.query(Work).count()

        assert work_count_after_first == work_count_after_second

    def test_idempotence_actions_say_already(self, session):
        """Re-applying the same proposal: second-run actions contain 'already'."""
        program = _make_program(session, "Idempotent Actions Prog")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        proposal = propose_segmentation(session, program)

        apply_segmentation(session, proposal, dry_run=False)
        actions2 = apply_segmentation(session, proposal, dry_run=False)

        assert any("already" in a for a in actions2)

    # --- 8.4 Empty-work deletion rules ---

    def test_empty_work_deleted(self, session):
        """Old work left with 0 episodes and no MANUAL rows is deleted."""
        program = _make_program(session, "Delete Empty Work Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        old_work_ids = [ep.work_id for ep in eps]
        proposal = propose_segmentation(session, program)

        actions = apply_segmentation(session, proposal, dry_run=False)

        # The old catch-all works (no MANUAL rows) should be gone
        for wid in old_work_ids:
            assert session.get(Work, wid) is None, (
                f"Expected work #{wid} to be deleted but it still exists"
            )
        assert any("delete" in a for a in actions)

    def test_empty_work_kept_if_manual_row(self, session):
        """Old work with a MANUAL MetadataValue row is NOT deleted even with 0 episodes."""
        program = _make_program(session, "Keep Work Manual Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        # Add a MANUAL row to the first episode's old work
        first_old_work_id = eps[0].work_id
        session.add(
            MetadataValue(
                entity_type="work",
                entity_id=first_old_work_id,
                field="author",
                value="Jakub Wassermann",
                origin=FieldOrigin.MANUAL,
                source="user",
            )
        )
        session.flush()

        proposal = propose_segmentation(session, program)
        actions = apply_segmentation(session, proposal, dry_run=False)

        # Work with MANUAL row must survive
        assert session.get(Work, first_old_work_id) is not None
        assert any("keep" in a for a in actions)

    # --- 8.5 expected_total note ---

    def test_expected_total_note_emitted(self, session):
        """When old work has expected_total MANUAL row, action note is emitted."""
        program = _make_program(session, "Expected Total Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        old_work_id = eps[0].work_id
        session.add(
            MetadataValue(
                entity_type="work",
                entity_id=old_work_id,
                field="expected_total",
                value="2",
                origin=FieldOrigin.MANUAL,
                source="user",
            )
        )
        session.flush()

        proposal = propose_segmentation(session, program)
        actions = apply_segmentation(session, proposal, dry_run=False)

        assert any("expected_total" in a and "review" in a for a in actions)

    # --- 8.6 Dry-run purity ---

    def test_dry_run_purity(self, session):
        """Dry run leaves episode work_ids and work count unchanged."""
        program = _make_program(session, "Dry Run Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        original_work_ids = [ep.work_id for ep in eps]
        original_work_count = session.query(Work).count()
        proposal = propose_segmentation(session, program)

        actions = apply_segmentation(session, proposal, dry_run=True)

        # No mutations: episodes still on original works
        session.expire_all()
        for ep, orig_wid in zip(eps, original_work_ids):
            session.refresh(ep)
            assert ep.work_id == orig_wid
        # Work count unchanged
        assert session.query(Work).count() == original_work_count
        # Actions still describe the intended operations
        assert actions  # non-empty action list

    # --- 8.7 only_titles filter ---

    def test_only_titles_filter(self, session):
        """only_titles skips ProposedWorks not in the set."""
        program = _make_program(session, "Only Titles Prog")
        eps = _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
                "Karel Čapek: RUR",
            ],
        )
        kolumbus_ep_ids = {eps[0].id, eps[1].id}
        rur_ep_id = eps[2].id
        rur_old_work_id = eps[2].work_id

        proposal = propose_segmentation(session, program)
        apply_segmentation(session, proposal, dry_run=False, only_titles={"Kolumbus"})

        # Kolumbus episodes should be re-parented
        session.refresh(eps[0])
        session.refresh(eps[1])
        kolumbus_work_ids = {eps[0].work_id, eps[1].work_id}
        assert len(kolumbus_work_ids) == 1  # merged

        # RUR episode should be untouched
        session.refresh(eps[2])
        assert eps[2].work_id == rur_old_work_id

    # --- 8.8 Author provenance ---

    def test_author_provenance_recorded(self, session):
        """Author is recorded as SCRAPED/segmentation on the new work."""
        program = _make_program(session, "Provenance Prog")
        _add_episodes(
            session,
            program,
            [
                "Jakub Wassermann: Kolumbus (1/2)",
                "Jakub Wassermann: Kolumbus (2/2)",
            ],
        )
        proposal = propose_segmentation(session, program)
        apply_segmentation(session, proposal, dry_run=False)

        # Find the new work
        new_work = session.query(Work).filter_by(title="Kolumbus").first()
        assert new_work is not None

        mv = (
            session.query(MetadataValue)
            .filter_by(
                entity_type="work",
                entity_id=new_work.id,
                field="author",
                origin=FieldOrigin.SCRAPED,
                source="segmentation",
            )
            .first()
        )
        assert mv is not None
        assert mv.value == "Jakub Wassermann"


# ---------------------------------------------------------------------------
# Identical author-prefixed titles WITHOUT part markers = one serialized book
# (mujrozhlas embeds all parts with IDENTICAL titles — found live:
#  "Margaret Atwoodová: Příběh služebnice" ×14 proposed as 14 one-ep works)
# ---------------------------------------------------------------------------


class TestIdenticalTitlesCluster:
    def _propose(self, session):
        program = _make_program(session, "Četba s hvězdičkou")
        _add_episodes(session, program, [
            "Margaret Atwoodová: Příběh služebnice",
            "Margaret Atwoodová: Příběh služebnice",
            "Margaret Atwoodová: Příběh služebnice",
            "Karel Horký: Nad mrtvým netopýrem",
        ])
        return propose_segmentation(session, program)

    def test_identical_titles_become_one_work(self, session):
        proposal = self._propose(session)
        atwood = [p for p in proposal.proposed if p.title == "Příběh služebnice"]
        assert len(atwood) == 1, "identical (author, title) episodes = ONE book"
        assert len(atwood[0].episode_ids) == 3

    def test_parts_ordered_by_episode_number(self, session):
        proposal = self._propose(session)
        atwood = [p for p in proposal.proposed if p.title == "Příběh služebnice"][0]
        assert list(atwood.episode_ids) == sorted(atwood.episode_ids)

    def test_singleton_stays_per_episode(self, session):
        proposal = self._propose(session)
        horky = [p for p in proposal.proposed if p.author == "Karel Horký"]
        assert len(horky) == 1
        assert len(horky[0].episode_ids) == 1
