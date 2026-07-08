"""Tests for library.pipelines.finalize — per-work folder finalization.

TDD order: tests written before implementation — expect RED on first run.

Uses the shared conftest db_session fixture and tmp_path for a real
filesystem library root (finalize moves actual files).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType,
    Episode, Program, Series, Station, Work,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def library_dir(tmp_path: Path) -> Path:
    d = tmp_path / "library"
    d.mkdir()
    return d


@pytest.fixture()
def work_with_episodes(db_session, library_dir):
    """Full hierarchy: Station→Program→Series→Work (expected_total=2)
    + 2 episodes each with a COMPLETE audio asset pointing at a real file."""
    station = Station(code="tst", name="Test Station")
    db_session.add(station)
    db_session.flush()

    program = Program(station_id=station.id, name="Test Program")
    db_session.add(program)
    db_session.flush()

    series = Series(program_id=program.id, name="Test Series")
    db_session.add(series)
    db_session.flush()

    work = Work(
        series_id=series.id,
        title="Great Book",
        author="Author One",
        year=2023,
        expected_total=2,
        expected_source="manual",
    )
    db_session.add(work)
    db_session.flush()

    audio_dir = library_dir / "Test Program (tst)"
    audio_dir.mkdir(parents=True, exist_ok=True)

    episodes = []
    for i in range(1, 3):
        ep = Episode(
            work_id=work.id,
            title=f"Episode {i}",
            ext_id=f"ext-{i}",
            episode_number=i,
            url=f"https://example.cz/ep-{i}",
        )
        db_session.add(ep)
        db_session.flush()

        audio_file = audio_dir / f"Author One - (2023) Great Book - 0{i} Episode {i}.m4a"
        audio_file.write_bytes(b"fake audio data")

        asset = Asset(
            episode_id=ep.id,
            type=AssetType.AUDIO,
            status=AssetStatus.COMPLETE,
            file_path=str(audio_file),
        )
        db_session.add(asset)
        db_session.flush()
        episodes.append(ep)

    db_session.flush()
    return work, episodes


def _audio_asset(db_session, ep):
    return (
        db_session.query(Asset)
        .filter_by(episode_id=ep.id, type=AssetType.AUDIO)
        .first()
    )


# ---------------------------------------------------------------------------
# plan_finalize
# ---------------------------------------------------------------------------

class TestPlanFinalize:
    def test_returns_nonempty_action_list(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import plan_finalize

        work, _ = work_with_episodes
        actions = plan_finalize(db_session, work, library_dir)
        assert len(actions) > 0

    def test_actions_mention_work_folder(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import plan_finalize

        work, _ = work_with_episodes
        combined = "\n".join(plan_finalize(db_session, work, library_dir))
        assert "Author One" in combined
        assert "Great Book" in combined

    def test_actions_mention_each_episode_audio_file(
        self, db_session, work_with_episodes, library_dir
    ):
        from audiobiblio.library.pipelines.finalize import plan_finalize

        work, _ = work_with_episodes
        combined = "\n".join(plan_finalize(db_session, work, library_dir))
        assert ".m4a" in combined

    def test_does_not_move_files(self, db_session, work_with_episodes, library_dir):
        """plan_finalize is a pure dry-run — files must stay in place."""
        from audiobiblio.library.pipelines.finalize import plan_finalize

        work, episodes = work_with_episodes
        original = [_audio_asset(db_session, ep).file_path for ep in episodes]
        plan_finalize(db_session, work, library_dir)
        for p in original:
            assert Path(p).exists(), f"plan_finalize must not move {p}"


# ---------------------------------------------------------------------------
# finalize_work — dry run
# ---------------------------------------------------------------------------

class TestFinalizeWorkDryRun:
    def test_dry_run_returns_applied_false(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        report = finalize_work(db_session, work, library_dir, dry_run=True)
        assert report.applied is False

    def test_dry_run_returns_nonempty_actions(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        report = finalize_work(db_session, work, library_dir, dry_run=True)
        assert len(report.actions) > 0

    def test_dry_run_does_not_move_files(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        original = [_audio_asset(db_session, ep).file_path for ep in episodes]
        finalize_work(db_session, work, library_dir, dry_run=True)
        for p in original:
            assert Path(p).exists(), f"dry_run must not move {p}"

    def test_dry_run_moved_is_zero(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        report = finalize_work(db_session, work, library_dir, dry_run=True)
        assert report.moved == 0


# ---------------------------------------------------------------------------
# finalize_work — apply
# ---------------------------------------------------------------------------

class TestFinalizeWorkApply:
    def test_apply_returns_applied_true(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        report = finalize_work(db_session, work, library_dir, dry_run=False)
        assert report.applied is True

    def test_apply_moves_all_audio_files(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        original = [_audio_asset(db_session, ep).file_path for ep in episodes]
        report = finalize_work(db_session, work, library_dir, dry_run=False)

        assert report.moved == len(episodes)
        for p in original:
            assert not Path(p).exists(), f"File must be moved from {p}"

    def test_apply_updates_asset_file_path_in_db(
        self, db_session, work_with_episodes, library_dir
    ):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        finalize_work(db_session, work, library_dir, dry_run=False)

        for ep in episodes:
            asset = _audio_asset(db_session, ep)
            assert asset.file_path is not None
            assert Path(asset.file_path).exists(), (
                f"Updated DB path must point to existing file: {asset.file_path}"
            )

    def test_files_never_deleted(self, db_session, work_with_episodes, library_dir):
        """Files are moved (not deleted) — old path gone, new path exists."""
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        asset = _audio_asset(db_session, episodes[0])
        original_path = Path(asset.file_path)

        finalize_work(db_session, work, library_dir, dry_run=False)

        db_session.expire(asset)
        new_path = Path(asset.file_path)
        assert not original_path.exists(), "File must not remain at original path"
        assert new_path.exists(), f"File must exist at new path {new_path}"

    def test_apply_no_errors_for_complete_work(
        self, db_session, work_with_episodes, library_dir
    ):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        report = finalize_work(db_session, work, library_dir, dry_run=False)
        assert len(report.errors) == 0

    def test_apply_creates_per_work_subfolder(
        self, db_session, work_with_episodes, library_dir
    ):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        finalize_work(db_session, work, library_dir, dry_run=False)

        # Target dir: library/Test Program (tst)/Author One - (2023) Great Book/
        program_dir = library_dir / "Test Program (tst)"
        assert program_dir.is_dir()
        work_dirs = [d for d in program_dir.iterdir() if d.is_dir()]
        assert len(work_dirs) == 1, f"Expected 1 work dir, found: {work_dirs}"
        folder_name = work_dirs[0].name
        assert "Author One" in folder_name
        assert "Great Book" in folder_name

    def test_existing_program_dir_never_renamed(
        self, db_session, work_with_episodes, library_dir
    ):
        """Binding rule: never rename existing directories — only create new ones."""
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, _ = work_with_episodes
        program_dir = library_dir / "Test Program (tst)"
        assert program_dir.is_dir()

        finalize_work(db_session, work, library_dir, dry_run=False)

        assert program_dir.is_dir(), "Existing program dir must survive untouched"


class TestCollisionHandling:
    def test_collision_adds_suffix(self, db_session, work_with_episodes, library_dir):
        """If a target file already exists, a -2 suffix is added before the extension."""
        from audiobiblio.library.pipelines.finalize import _derive_work_dir, finalize_work

        work, episodes = work_with_episodes
        ep = episodes[0]
        asset = _audio_asset(db_session, ep)

        # Pre-create the target file to force a collision
        target_dir = _derive_work_dir(work, ep, library_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / Path(asset.file_path).name).write_bytes(b"existing file")

        report = finalize_work(db_session, work, library_dir, dry_run=False)

        assert report.moved > 0
        stems = [f.stem for f in target_dir.iterdir() if f.is_file()]
        assert any("-2" in s for s in stems), f"Expected -2 suffix in {stems}"


class TestSidecarHandling:
    def test_sidecar_same_stem_moved(self, db_session, work_with_episodes, library_dir):
        """Sidecar files sharing the audio file's stem are moved to the work folder."""
        from audiobiblio.library.pipelines.finalize import _derive_work_dir, finalize_work

        work, episodes = work_with_episodes
        ep = episodes[0]
        audio_path = Path(_audio_asset(db_session, ep).file_path)

        sidecar = audio_path.parent / f"{audio_path.stem}.nfo"
        sidecar.write_bytes(b"<nfo>test</nfo>")

        finalize_work(db_session, work, library_dir, dry_run=False)

        assert not sidecar.exists(), "Sidecar must be moved from original location"
        target_dir = _derive_work_dir(work, ep, library_dir)
        moved = target_dir / sidecar.name
        moved_2 = target_dir / f"{audio_path.stem}-2.nfo"
        assert moved.exists() or moved_2.exists(), (
            f"Sidecar should be in {target_dir}; found: {list(target_dir.iterdir())}"
        )

    def test_tracked_sidecar_asset_path_updated_in_db(
        self, db_session, work_with_episodes, library_dir
    ):
        """A tracked asset moved via the sidecar sweep must get its DB path updated."""
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        ep = episodes[0]
        audio_path = Path(_audio_asset(db_session, ep).file_path)

        # Tracked META_JSON asset sharing the audio file's stem (e.g. foo.json)
        meta_file = audio_path.parent / f"{audio_path.stem}.json"
        meta_file.write_bytes(b"{}")
        meta_asset = Asset(
            episode_id=ep.id,
            type=AssetType.META_JSON,
            status=AssetStatus.COMPLETE,
            file_path=str(meta_file),
        )
        db_session.add(meta_asset)
        db_session.flush()

        finalize_work(db_session, work, library_dir, dry_run=False)

        db_session.expire(meta_asset)
        assert meta_asset.file_path != str(meta_file)
        assert Path(meta_asset.file_path).exists(), (
            f"Tracked sidecar DB path must follow the move: {meta_asset.file_path}"
        )

    def test_sidecar_mentioned_in_actions(self, db_session, work_with_episodes, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        work, episodes = work_with_episodes
        audio_path = Path(_audio_asset(db_session, episodes[0]).file_path)
        sidecar = audio_path.parent / f"{audio_path.stem}.nfo"
        sidecar.write_bytes(b"<nfo/>")

        report = finalize_work(db_session, work, library_dir, dry_run=True)
        assert ".nfo" in "\n".join(report.actions)


class TestEmptyWork:
    def test_empty_work_returns_error_in_report(self, db_session, library_dir):
        from audiobiblio.library.pipelines.finalize import finalize_work

        station = Station(code="tst2", name="S2")
        db_session.add(station)
        db_session.flush()
        program = Program(station_id=station.id, name="P2")
        db_session.add(program)
        db_session.flush()
        series = Series(program_id=program.id, name="Se2")
        db_session.add(series)
        db_session.flush()
        work = Work(series_id=series.id, title="Empty Work")
        db_session.add(work)
        db_session.flush()

        report = finalize_work(db_session, work, library_dir, dry_run=False)
        assert len(report.errors) > 0
        assert report.moved == 0


# ---------------------------------------------------------------------------
# completed_works (pipelines.completeness)
# ---------------------------------------------------------------------------

class TestCompletedWorks:
    def test_complete_work_listed_with_have(self, db_session, work_with_episodes):
        from audiobiblio.library.pipelines.completeness import completed_works

        work, episodes = work_with_episodes
        rows = completed_works(db_session)
        assert (work.id, len(episodes)) in [(w.id, have) for w, have in rows]

    def test_incomplete_work_not_listed(self, db_session, work_with_episodes):
        from audiobiblio.library.pipelines.completeness import completed_works

        work, episodes = work_with_episodes
        asset = _audio_asset(db_session, episodes[0])
        asset.status = AssetStatus.MISSING
        db_session.flush()

        rows = completed_works(db_session)
        assert work.id not in [w.id for w, _ in rows]

    def test_work_without_expected_total_not_listed(self, db_session, work_with_episodes):
        from audiobiblio.library.pipelines.completeness import completed_works

        work, _ = work_with_episodes
        work.expected_total = None
        db_session.flush()

        rows = completed_works(db_session)
        assert work.id not in [w.id for w, _ in rows]
