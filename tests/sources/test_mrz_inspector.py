"""Tests for mrz_inspector module."""
from __future__ import annotations

import pytest

from audiobiblio.sources.mrz_inspector import parent_url, RESERVED_SLUGS, classify_probe


PAGE_URL = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/margaret-attwoodova-pribeh-sluzebnice"


def _make_atwood_probe():
    """Canned flat probe: 12 parts, same page URL, distinct yt-dlp ids/episode_numbers."""
    entries = []
    for i in range(12):
        entries.append({
            "id": str(12087683 + i),
            "title": "Příběh služebnice",
            "webpage_url": PAGE_URL,
            "episode_number": i + 1,
            "duration": 1800.0 + i * 60,
            "extractor_key": "MujRozhlas",
        })
    return {
        "title": "Příběh služebnice",
        "extractor_key": "MujRozhlas",
        "entries": entries,
    }


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


class TestClassifyProbePartIdentity:
    """EpisodeItem must carry ext_id and duration_s from yt-dlp entries."""

    def test_classify_probe_preserves_ext_id(self):
        """ext_id of each EpisodeItem equals str(12087683+i)."""
        probe = _make_atwood_probe()
        result = classify_probe(probe, PAGE_URL)
        assert len(result.entries) == 12
        for i, item in enumerate(result.entries):
            assert item.ext_id == str(12087683 + i), (
                f"item[{i}].ext_id expected {12087683 + i!r}, got {item.ext_id!r}"
            )

    def test_classify_probe_preserves_episode_number(self):
        """episode_numbers are 1..12."""
        probe = _make_atwood_probe()
        result = classify_probe(probe, PAGE_URL)
        numbers = [item.episode_number for item in result.entries]
        assert numbers == list(range(1, 13)), f"episode_numbers: {numbers}"

    def test_classify_probe_preserves_duration_s(self):
        """duration_s of item[0] == 1800.0."""
        probe = _make_atwood_probe()
        result = classify_probe(probe, PAGE_URL)
        assert result.entries[0].duration_s == 1800.0

    def test_classify_probe_single_item_preserves_ext_id_and_duration(self):
        """Single-item (non-playlist) probe must also map id/duration."""
        data = {
            "id": "12087683",
            "title": "Příběh služebnice",
            "webpage_url": PAGE_URL,
            "episode_number": 1,
            "duration": 1800.0,
            "extractor_key": "MujRozhlas",
        }
        result = classify_probe(data, PAGE_URL)
        assert len(result.entries) == 1
        item = result.entries[0]
        assert item.ext_id == "12087683"
        assert item.duration_s == 1800.0
        assert item.episode_number == 1
