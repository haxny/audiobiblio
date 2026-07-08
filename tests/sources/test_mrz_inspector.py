"""Tests for mrz_inspector module."""
from __future__ import annotations

import pytest

from audiobiblio.sources.mrz_inspector import parent_url, RESERVED_SLUGS


class TestParentUrl:
    """Tests for parent_url function."""

    def test_episode_url_returns_parent(self):
        """Episode URL (depth >= 2) should return parent program URL."""
        url = "https://www.mujrozhlas.cz/hajaja/ep-nazev"
        result = parent_url(url)
        assert result == "https://www.mujrozhlas.cz/hajaja"

    def test_program_url_returns_none(self):
        """Program URL (depth 1) should return None."""
        url = "https://www.mujrozhlas.cz/hajaja"
        result = parent_url(url)
        assert result is None

    def test_non_mujrozhlas_returns_none(self):
        """Non-mujrozhlas URLs should return None."""
        url = "https://www.rozhlas.cz/some/path"
        result = parent_url(url)
        assert result is None

    def test_reserved_slug_episode_returns_none(self):
        """Reserved first segment '/episode/<uuid>' should return None."""
        url = "https://www.mujrozhlas.cz/episode/abc-123"
        result = parent_url(url)
        assert result is None

    def test_reserved_slugs_frozenset_defined(self):
        """RESERVED_SLUGS should include 'episode'."""
        assert "episode" in RESERVED_SLUGS
        assert isinstance(RESERVED_SLUGS, frozenset)
