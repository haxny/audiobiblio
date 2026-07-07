"""Tests for library.pipelines.completeness — work_completeness + incomplete_works.

Design: pure unit tests against an in-memory SQLite DB.  Uses the shared
conftest db_session fixture (Base.metadata.create_all on an in-memory engine).
"""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType,
    Episode, Program, Series, Station, Work,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mk_station(db, code: str = "tst") -> Station:
    s = db.query(Station).filter_by(code=code).first()
    if s:
        return s
    s = Station(code=code, name=f"Station {code}")
    db.add(s)
    db.flush()
    return s


def _mk_series(db, *, code: str = "tst") -> Series:
    st = _mk_station(db, code=code)
    p = Program(station_id=st.id, name=f"Prog-{code}")
    db.add(p)
    db.flush()
    s = Series(program_id=p.id, name=f"Series-{code}")
    db.add(s)
    db.flush()
    return s


def _mk_work(db, series: Series, *, expected_total: int | None = None) -> Work:
    w = Work(series_id=series.id, title=f"Work-{id(series)}-{expected_total}",
             expected_total=expected_total)
    db.add(w)
    db.flush()
    return w


_counter = {"n": 0}


def _mk_episode(db, work: Work, *, episode_number: int | None = None) -> Episode:
    _counter["n"] += 1
    ep = Episode(
        work_id=work.id,
        title=f"Ep-{_counter['n']}",
        episode_number=episode_number,
        url=f"https://example.cz/ep-{_counter['n']}",
    )
    db.add(ep)
    db.flush()
    return ep


def _mk_audio_complete(db, ep: Episode) -> Asset:
    a = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
        file_path=f"/fake/{ep.id}.m4a",
    )
    db.add(a)
    db.flush()
    return a


# ---------------------------------------------------------------------------
# work_completeness
# ---------------------------------------------------------------------------

class TestWorkCompleteness:
    def test_no_expected_total_expected_is_none(self, db_session):
        """expected=None when work.expected_total is not set."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c1")
        work = _mk_work(db_session, series)
        ep = _mk_episode(db_session, work, episode_number=1)
        _mk_audio_complete(db_session, ep)

        result = work_completeness(db_session, work)
        assert result.have == 1
        assert result.expected is None
        assert result.missing_numbers is None

    def test_all_complete_missing_numbers_empty(self, db_session):
        """When have == expected and numbering trustworthy, missing_numbers = []."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c2")
        work = _mk_work(db_session, series, expected_total=3)
        for i in range(1, 4):
            ep = _mk_episode(db_session, work, episode_number=i)
            _mk_audio_complete(db_session, ep)

        result = work_completeness(db_session, work)
        assert result.have == 3
        assert result.expected == 3
        assert result.missing_numbers == []

    def test_gap_in_dense_numbering_shows_missing(self, db_session):
        """Dense numbering → missing_numbers lists the missing episode numbers."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c3")
        work = _mk_work(db_session, series, expected_total=5)
        for i in [1, 2, 4, 5]:
            ep = _mk_episode(db_session, work, episode_number=i)
            _mk_audio_complete(db_session, ep)
        # ep 3 is missing entirely

        result = work_completeness(db_session, work)
        assert result.have == 4
        assert result.expected == 5
        assert result.missing_numbers == [3]

    def test_sparse_numbering_missing_numbers_none(self, db_session):
        """< 80% of episodes have positive episode_number → missing_numbers = None."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c4")
        work = _mk_work(db_session, series, expected_total=10)
        # 5 episodes, only 2 have distinct positive numbers → 40 % < 80 %
        for i in range(1, 6):
            num = i if i <= 2 else None
            ep = _mk_episode(db_session, work, episode_number=num)
            _mk_audio_complete(db_session, ep)

        result = work_completeness(db_session, work)
        assert result.have == 5
        assert result.expected == 10
        assert result.missing_numbers is None

    def test_exactly_80_percent_is_trustworthy(self, db_session):
        """At the 80 % boundary the heuristic considers numbering trustworthy."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c5")
        work = _mk_work(db_session, series, expected_total=5)
        # 5 episodes: 4 have distinct positive numbers (exactly 80 %)
        for i in range(1, 5):
            ep = _mk_episode(db_session, work, episode_number=i)
            _mk_audio_complete(db_session, ep)
        ep5 = _mk_episode(db_session, work, episode_number=None)
        _mk_audio_complete(db_session, ep5)

        result = work_completeness(db_session, work)
        # 4/5 = 80 % → trustworthy → missing_numbers computed
        assert result.missing_numbers is not None

    def test_have_counts_only_complete_audio(self, db_session):
        """Episodes with MISSING or no audio do not increment 'have'."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c6")
        work = _mk_work(db_session, series, expected_total=3)

        ep1 = _mk_episode(db_session, work, episode_number=1)
        _mk_audio_complete(db_session, ep1)

        ep2 = _mk_episode(db_session, work, episode_number=2)
        db_session.add(
            Asset(episode_id=ep2.id, type=AssetType.AUDIO, status=AssetStatus.MISSING)
        )
        db_session.flush()

        _mk_episode(db_session, work, episode_number=3)  # no asset at all

        result = work_completeness(db_session, work)
        assert result.have == 1
        assert result.missing_numbers == [2, 3]

    def test_zero_episodes_have_zero(self, db_session):
        """Work with no episodes at all: have=0, expected=N, missing_numbers=None."""
        from audiobiblio.library.pipelines.completeness import work_completeness

        series = _mk_series(db_session, code="c7")
        work = _mk_work(db_session, series, expected_total=5)

        result = work_completeness(db_session, work)
        assert result.have == 0
        assert result.expected == 5
        # No episodes → numbering not trustworthy → None
        assert result.missing_numbers is None


# ---------------------------------------------------------------------------
# incomplete_works
# ---------------------------------------------------------------------------

class TestIncompleteWorks:
    def test_returns_works_with_gap(self, db_session):
        """Works with expected_total set and have < expected_total appear."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series = _mk_series(db_session, code="iw1")
        work = _mk_work(db_session, series, expected_total=3)
        ep = _mk_episode(db_session, work, episode_number=1)
        _mk_audio_complete(db_session, ep)

        results = incomplete_works(db_session)
        assert len(results) == 1
        w, have = results[0]
        assert w.id == work.id
        assert have == 1

    def test_excludes_complete_works(self, db_session):
        """Works where have >= expected_total are excluded."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series = _mk_series(db_session, code="iw2")
        work = _mk_work(db_session, series, expected_total=2)
        for i in [1, 2]:
            ep = _mk_episode(db_session, work, episode_number=i)
            _mk_audio_complete(db_session, ep)

        results = incomplete_works(db_session)
        assert results == []

    def test_excludes_works_without_expected_total(self, db_session):
        """Works without expected_total are excluded."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series = _mk_series(db_session, code="iw3")
        work = _mk_work(db_session, series)  # no expected_total
        ep = _mk_episode(db_session, work, episode_number=1)
        _mk_audio_complete(db_session, ep)

        results = incomplete_works(db_session)
        assert results == []

    def test_ordering_smallest_gap_first(self, db_session):
        """Works sorted by (expected - have) ascending: most nearly complete first."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series_a = _mk_series(db_session, code="iw4a")
        series_b = _mk_series(db_session, code="iw4b")

        # work_a: gap = 2 (have=3, expected=5)
        work_a = _mk_work(db_session, series_a, expected_total=5)
        for i in range(1, 4):
            ep = _mk_episode(db_session, work_a, episode_number=i)
            _mk_audio_complete(db_session, ep)

        # work_b: gap = 1 (have=4, expected=5)
        work_b = _mk_work(db_session, series_b, expected_total=5)
        for i in range(1, 5):
            ep = _mk_episode(db_session, work_b, episode_number=i)
            _mk_audio_complete(db_session, ep)

        results = incomplete_works(db_session)
        ids = [w.id for w, _ in results]
        # work_b has the smaller gap (1 < 2) → comes first
        assert ids[0] == work_b.id
        assert ids[1] == work_a.id

    def test_limit_caps_result_set(self, db_session):
        """limit parameter caps the number of rows returned."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series = _mk_series(db_session, code="iw5")
        for i in range(5):
            w = Work(series_id=series.id, title=f"Lim-{i}", expected_total=10)
            db_session.add(w)
            db_session.flush()

        results = incomplete_works(db_session, limit=2)
        assert len(results) <= 2

    def test_have_in_result_matches_audio_count(self, db_session):
        """The 'have' value in results matches actual complete-audio count."""
        from audiobiblio.library.pipelines.completeness import incomplete_works

        series = _mk_series(db_session, code="iw6")
        work = _mk_work(db_session, series, expected_total=5)
        # 2 complete, 1 missing-asset, 1 no-asset
        for i in range(1, 3):
            ep = _mk_episode(db_session, work, episode_number=i)
            _mk_audio_complete(db_session, ep)
        ep_miss = _mk_episode(db_session, work, episode_number=3)
        db_session.add(
            Asset(episode_id=ep_miss.id, type=AssetType.AUDIO, status=AssetStatus.MISSING)
        )
        db_session.flush()
        _mk_episode(db_session, work, episode_number=4)  # no asset

        results = incomplete_works(db_session)
        assert len(results) == 1
        _, have = results[0]
        assert have == 2
