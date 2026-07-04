"""Tests for find_duplicate_clusters and merge_episodes (dedupe/clusters.py).

TDD: these tests are written RED-first; implementation fills them GREEN.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    EpisodeAlias,
    Episode,
    FieldOrigin,
    MetadataValue,
)
from audiobiblio.dedupe.clusters import (
    ManualMetadataProtectionError,
    find_duplicate_clusters,
    merge_episodes,
)


# ---------------------------------------------------------------------------
# Tier A — shared stripped URL (COMPLETE audio only)
# ---------------------------------------------------------------------------


class TestFindDuplicateClustersURL:
    def test_finds_shared_stripped_url(self, db_session, episode_factory):
        """Two episodes with same norm_url_strip_reair → one Tier-A cluster."""
        ep1 = episode_factory()
        ep2 = episode_factory()
        ep1.url = "https://mujrozhlas.cz/hra/osada-2941669"
        ep2.url = "https://mujrozhlas.cz/hra/osada-3000001"
        a1 = Asset(episode_id=ep1.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        a2 = Asset(episode_id=ep2.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        db_session.add_all([a1, a2])
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        url_clusters = [c for c in clusters if c["reason"] == "same_stripped_url"]
        assert len(url_clusters) == 1
        ep_ids = {e.id for e in url_clusters[0]["episodes"]}
        assert ep1.id in ep_ids
        assert ep2.id in ep_ids

    def test_distinct_urls_no_cluster(self, db_session, episode_factory):
        ep1 = episode_factory()
        ep2 = episode_factory()
        ep1.url = "https://mujrozhlas.cz/hra/osada"
        ep2.url = "https://mujrozhlas.cz/hra/zahrada"
        a1 = Asset(episode_id=ep1.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        a2 = Asset(episode_id=ep2.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        db_session.add_all([a1, a2])
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        url_clusters = [c for c in clusters if c["reason"] == "same_stripped_url"]
        assert len(url_clusters) == 0

    def test_non_complete_audio_excluded(self, db_session, episode_factory):
        """An episode without COMPLETE audio is invisible to Tier A."""
        ep1 = episode_factory()
        ep2 = episode_factory()
        ep1.url = "https://mujrozhlas.cz/hra/osada-2941669"
        ep2.url = "https://mujrozhlas.cz/hra/osada-3000001"
        a1 = Asset(episode_id=ep1.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        a2 = Asset(episode_id=ep2.id, type=AssetType.AUDIO, status=AssetStatus.FAILED)
        db_session.add_all([a1, a2])
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        url_clusters = [c for c in clusters if c["reason"] == "same_stripped_url"]
        assert len(url_clusters) == 0

    def test_cluster_key_is_stripped_url(self, db_session, episode_factory):
        ep1 = episode_factory()
        ep2 = episode_factory()
        ep1.url = "https://mujrozhlas.cz/hra/osada-2941669"
        ep2.url = "https://mujrozhlas.cz/hra/osada-3000001"
        for ep in (ep1, ep2):
            db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE))
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        url_clusters = [c for c in clusters if c["reason"] == "same_stripped_url"]
        assert url_clusters[0]["key"] == "https://mujrozhlas.cz/hra/osada"


# ---------------------------------------------------------------------------
# Tier B — fuzzy title matching within program
# ---------------------------------------------------------------------------


class TestFindDuplicateClustersFuzzy:
    def test_fuzzy_title_within_same_program(self, db_session, episode_factory):
        ep1 = episode_factory("Osada")
        ep2 = episode_factory("Osada")
        ep1.title = "Bila nemoc, cast prvni"
        ep2.title = "Bila nemoc, cast prvni"  # identical after normalisation
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        fuzzy = [c for c in clusters if c["reason"] == "fuzzy_title_same_program"]
        ep_ids = {e.id for c in fuzzy for e in c["episodes"]}
        assert ep1.id in ep_ids
        assert ep2.id in ep_ids

    def test_generic_titles_excluded_from_tier_b(self, db_session, episode_factory):
        """Titles in _GENERIC_TITLES must NOT produce a cluster."""
        ep1 = episode_factory("GenericProg")
        ep2 = episode_factory("GenericProg")
        ep1.title = "Epizody pořadu"
        ep2.title = "Epizody pořadu"
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        fuzzy = [c for c in clusters if c["reason"] == "fuzzy_title_same_program"]
        ep_ids = {e.id for c in fuzzy for e in c["episodes"]}
        assert ep1.id not in ep_ids
        assert ep2.id not in ep_ids

    def test_cross_program_titles_not_matched(self, db_session, episode_factory):
        """Similar titles in different programs must NOT cluster."""
        ep1 = episode_factory("ProgA")
        ep2 = episode_factory("ProgB")
        ep1.title = "Bila nemoc, cast prvni"
        ep2.title = "Bila nemoc, cast prvni"
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        fuzzy = [c for c in clusters if c["reason"] == "fuzzy_title_same_program"]
        for c in fuzzy:
            ids = {e.id for e in c["episodes"]}
            assert not (ep1.id in ids and ep2.id in ids), (
                "Cross-program match must not occur"
            )

    def test_dissimilar_titles_not_matched(self, db_session, episode_factory):
        ep1 = episode_factory("Prog")
        ep2 = episode_factory("Prog")
        ep1.title = "Bila nemoc"
        ep2.title = "Zahrada"
        db_session.flush()

        clusters = find_duplicate_clusters(db_session)
        fuzzy = [c for c in clusters if c["reason"] == "fuzzy_title_same_program"]
        ep_ids = {e.id for c in fuzzy for e in c["episodes"]}
        assert ep1.id not in ep_ids or ep2.id not in ep_ids


# ---------------------------------------------------------------------------
# Limit cap
# ---------------------------------------------------------------------------


class TestCapLimit:
    def test_limit_respected(self, db_session, episode_factory):
        """find_duplicate_clusters(limit=1) returns at most 1 cluster."""
        # Create 4 episodes with same stripped URL → 1 Tier-A cluster
        base = "https://mujrozhlas.cz/hra/story"
        eps = [episode_factory() for _ in range(4)]
        for i, ep in enumerate(eps):
            ep.url = f"{base}-{2000000 + i}"
            db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE))
        db_session.flush()

        clusters = find_duplicate_clusters(db_session, limit=1)
        assert len(clusters) <= 1


# ---------------------------------------------------------------------------
# merge_episodes — dry run
# ---------------------------------------------------------------------------


class TestMergeEpisodesDryRun:
    def test_returns_action_list(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        dup.url = "https://mujrozhlas.cz/dup"
        audio_file = tmp_path / "dup.m4a"
        audio_file.write_bytes(b"fake audio")
        db_session.add(Asset(
            episode_id=dup.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
            file_path=str(audio_file),
        ))
        db_session.flush()

        actions = merge_episodes(db_session, canonical.id, dup.id, tmp_path, dry_run=True)

        assert isinstance(actions, list)
        assert len(actions) > 0
        assert any("alias" in a for a in actions)
        assert any("trash" in a.lower() for a in actions)

    def test_dry_run_makes_no_db_changes(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        db_session.flush()

        merge_episodes(db_session, canonical.id, dup.id, tmp_path, dry_run=True)

        # Episode still exists
        assert db_session.get(Episode, dup.id) is not None


# ---------------------------------------------------------------------------
# merge_episodes — real run
# ---------------------------------------------------------------------------


class TestMergeEpisodesReal:
    def test_deletes_duplicate_episode(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        dup.url = "https://mujrozhlas.cz/dup-real"
        audio_file = tmp_path / "dup.m4a"
        audio_file.write_bytes(b"fake audio")
        db_session.add(Asset(
            episode_id=dup.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
            file_path=str(audio_file),
        ))
        db_session.flush()

        trashed: list[Path] = []

        def fake_trash(p: Path) -> Path:
            trashed.append(p)
            return tmp_path / ".trash" / p.name

        merge_episodes(
            db_session, canonical.id, dup.id, tmp_path,
            dry_run=False, trash_fn=fake_trash,
        )

        assert db_session.get(Episode, dup.id) is None

    def test_deletes_duplicate_assets(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        audio_file = tmp_path / "dup2.m4a"
        audio_file.write_bytes(b"x")
        db_session.add(Asset(
            episode_id=dup.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
            file_path=str(audio_file),
        ))
        db_session.flush()

        merge_episodes(
            db_session, canonical.id, dup.id, tmp_path,
            dry_run=False, trash_fn=lambda p: p,
        )

        remaining = db_session.query(Asset).filter(Asset.episode_id == dup.id).count()
        assert remaining == 0

    def test_adds_alias_on_canonical(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        dup.url = "https://mujrozhlas.cz/alias-url"
        db_session.flush()

        merge_episodes(
            db_session, canonical.id, dup.id, tmp_path,
            dry_run=False, trash_fn=lambda p: p,
        )

        alias = (
            db_session.query(EpisodeAlias)
            .filter(
                EpisodeAlias.episode_id == canonical.id,
                EpisodeAlias.url == "https://mujrozhlas.cz/alias-url",
            )
            .first()
        )
        assert alias is not None

    def test_calls_trash_fn_for_audio_file(self, db_session, episode_factory, tmp_path):
        canonical = episode_factory()
        dup = episode_factory()
        audio_file = tmp_path / "audio.m4a"
        audio_file.write_bytes(b"audio data")
        db_session.add(Asset(
            episode_id=dup.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
            file_path=str(audio_file),
        ))
        db_session.flush()

        trashed: list[Path] = []

        def fake_trash(p: Path) -> Path:
            trashed.append(p)
            return tmp_path / ".trash" / p.name

        merge_episodes(
            db_session, canonical.id, dup.id, tmp_path,
            dry_run=False, trash_fn=fake_trash,
        )

        assert len(trashed) == 1
        assert trashed[0] == audio_file


# ---------------------------------------------------------------------------
# merge_episodes — MANUAL metadata guard (409-equivalent)
# ---------------------------------------------------------------------------


class TestMergeManualProtection:
    def test_raises_when_duplicate_has_manual_metadata(
        self, db_session, episode_factory, tmp_path
    ):
        canonical = episode_factory()
        dup = episode_factory()
        db_session.add(MetadataValue(
            entity_type="episode",
            entity_id=dup.id,
            field="title",
            value="Curated Title",
            origin=FieldOrigin.MANUAL,
            source="user",
        ))
        db_session.flush()

        with pytest.raises(ManualMetadataProtectionError):
            merge_episodes(db_session, canonical.id, dup.id, tmp_path, dry_run=True)

    def test_allows_merge_without_manual_metadata(
        self, db_session, episode_factory, tmp_path
    ):
        canonical = episode_factory()
        dup = episode_factory()
        # Only ENRICHED — must not block
        db_session.add(MetadataValue(
            entity_type="episode",
            entity_id=dup.id,
            field="title",
            value="Enriched Title",
            origin=FieldOrigin.ENRICHED,
            source="databazeknih",
        ))
        db_session.flush()

        # Should not raise
        actions = merge_episodes(db_session, canonical.id, dup.id, tmp_path, dry_run=True)
        assert isinstance(actions, list)
