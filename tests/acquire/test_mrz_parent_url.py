"""Unit tests for parent_url() in sources/mrz_inspector.py."""
import pytest

from audiobiblio.sources.mrz_inspector import parent_url


class TestParentUrl:
    """parent_url derives the program root from an episode URL."""

    def test_episode_depth2_returns_program(self):
        url = "https://www.mujrozhlas.cz/hajaja/nazev-epizody"
        assert parent_url(url) == "https://www.mujrozhlas.cz/hajaja"

    def test_episode_depth2_preserves_scheme_and_host(self):
        url = "https://www.mujrozhlas.cz/hrajeme-si/epizoda-1"
        result = parent_url(url)
        assert result == "https://www.mujrozhlas.cz/hrajeme-si"

    def test_program_depth1_returns_none(self):
        url = "https://www.mujrozhlas.cz/hajaja"
        assert parent_url(url) is None

    def test_root_depth0_returns_none(self):
        url = "https://www.mujrozhlas.cz/"
        assert parent_url(url) is None

    def test_non_mujrozhlas_returns_none(self):
        url = "https://www.example.com/foo/bar"
        assert parent_url(url) is None

    def test_youtube_returns_none(self):
        url = "https://www.youtube.com/watch?v=abc123"
        assert parent_url(url) is None

    def test_depth3_returns_depth1_parent(self):
        # depth 3: /program/series/episode → parent is /program
        url = "https://www.mujrozhlas.cz/cetba/romany/nazev-epizody"
        # depth >= 2 so parent = /cetba
        assert parent_url(url) == "https://www.mujrozhlas.cz/cetba"

    def test_trailing_slash_ignored(self):
        url = "https://www.mujrozhlas.cz/hajaja/epizoda/"
        assert parent_url(url) == "https://www.mujrozhlas.cz/hajaja"
