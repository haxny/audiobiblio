"""Tests for evaluate_reair decision function (spec §4.2).

Decision branches:
1. No COMPLETE AUDIO asset → return None
2. Both durations known, abs(diff) <= 5000 ms → return None (same content)
3. Both durations known, abs(diff) > 5000 ms → PENDING_REVIEW (ad-suspect)
4. Candidate duration unknown → PENDING_REVIEW with note "duration unknown"
5. Idempotent: existing (episode, url) candidate → return it unchanged
"""
import pytest

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, UpgradeStatus
from audiobiblio.dedupe.upgrades import evaluate_reair


def _complete_audio(session, episode, *, owned_duration_ms: int | None = 60_000):
    """Add a COMPLETE AUDIO asset and set episode.duration_ms."""
    episode.duration_ms = owned_duration_ms
    asset = Asset(
        episode_id=episode.id,
        type=AssetType.AUDIO,
        status=AssetStatus.COMPLETE,
    )
    session.add(asset)
    session.flush()
    return asset


class TestEvaluateReairBranches:
    def test_branch_no_complete_audio_returns_none(self, db_session, episode_factory):
        """Branch 1: no COMPLETE AUDIO asset → return None (normal re-download handles it)."""
        ep = episode_factory()
        ep.duration_ms = 60_000
        # Asset exists but is MISSING (default), not COMPLETE
        asset = Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.MISSING)
        db_session.add(asset)
        db_session.flush()

        result = evaluate_reair(
            db_session, ep,
            "https://example.cz/ep-1-2941669",
            candidate_duration_ms=58_000,
        )
        assert result is None

    def test_branch_within_tolerance_returns_none(self, db_session, episode_factory):
        """Branch 2: both durations known, diff <= 5000 ms → None (same content)."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=60_000)

        result = evaluate_reair(
            db_session, ep,
            "https://example.cz/ep-2-2941670",
            candidate_duration_ms=57_500,  # diff = 2500 <= 5000
        )
        assert result is None

    def test_branch_within_tolerance_boundary_exactly_5000(self, db_session, episode_factory):
        """Branch 2 boundary: diff exactly == 5000 ms → None (boundary is inclusive)."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=60_000)

        result = evaluate_reair(
            db_session, ep,
            "https://example.cz/ep-3-2941671",
            candidate_duration_ms=55_000,  # diff = 5000 exactly
        )
        assert result is None

    def test_branch_ad_suspect_beyond_tolerance_creates_pending_review(self, db_session, episode_factory):
        """Branch 3: both durations known, abs(diff) > 5000 ms → PENDING_REVIEW."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=60_000)

        candidate_url = "https://example.cz/ep-4-2941672"
        result = evaluate_reair(
            db_session, ep,
            candidate_url,
            candidate_duration_ms=53_000,  # diff = 7000 > 5000
        )

        assert result is not None
        assert result.status == UpgradeStatus.PENDING_REVIEW
        assert result.episode_id == ep.id
        assert result.candidate_duration_ms == 53_000
        assert result.owned_duration_ms == 60_000

    def test_branch_ad_suspect_shorter_candidate_also_flagged(self, db_session, episode_factory):
        """Branch 3: direction doesn't matter — shorter candidate still PENDING_REVIEW."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=50_000)

        result = evaluate_reair(
            db_session, ep,
            "https://example.cz/ep-5-2941673",
            candidate_duration_ms=58_000,  # candidate longer, diff = 8000 > 5000
        )
        assert result is not None
        assert result.status == UpgradeStatus.PENDING_REVIEW

    def test_branch_candidate_duration_unknown_creates_pending_review(self, db_session, episode_factory):
        """Branch 4: candidate duration unknown → PENDING_REVIEW with note 'duration unknown'."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=60_000)

        candidate_url = "https://example.cz/ep-6-2941674"
        result = evaluate_reair(
            db_session, ep,
            candidate_url,
            candidate_duration_ms=None,
        )

        assert result is not None
        assert result.status == UpgradeStatus.PENDING_REVIEW
        assert result.note is not None
        assert "duration unknown" in result.note.lower()

    def test_idempotency_returns_existing_unchanged(self, db_session, episode_factory):
        """Branch 5: existing (episode, url) candidate → return it unchanged, no duplicate."""
        ep = episode_factory()
        _complete_audio(db_session, ep, owned_duration_ms=60_000)

        candidate_url = "https://example.cz/ep-7-2941675"
        first = evaluate_reair(
            db_session, ep,
            candidate_url,
            candidate_duration_ms=None,
        )
        assert first is not None
        first_id = first.id

        # Second call with same args — must return the same row
        second = evaluate_reair(
            db_session, ep,
            candidate_url,
            candidate_duration_ms=None,
        )
        assert second is not None
        assert second.id == first_id

        # Verify only one row exists in DB
        from audiobiblio.core.db.models import UpgradeCandidate
        from audiobiblio.core.urls import norm_url
        count = (
            db_session.query(UpgradeCandidate)
            .filter_by(episode_id=ep.id, candidate_url=norm_url(candidate_url))
            .count()
        )
        assert count == 1
