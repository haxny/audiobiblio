"""Characterization tests for the 3-tier dedupe logic."""
from dataclasses import dataclass

from audiobiblio.dedupe.matching import (
    _norm_title,
    _norm_url,
    _norm_url_strip_reair,
    dedupe_discovered,
)


@dataclass
class FakeEntry:
    url: str | None = None
    title: str | None = None
    ext_id: str | None = None


class TestNormUrl:
    def test_lowercases_host_strips_slash(self):
        assert _norm_url("https://MujRozhlas.CZ/podcast/") == "https://mujrozhlas.cz/podcast"

    def test_none_is_empty(self):
        assert _norm_url(None) == ""

    def test_strips_query_and_fragment(self):
        assert _norm_url("https://a.cz/x?p=1#f") == "https://a.cz/x"


class TestNormUrlStripReair:
    def test_strips_seven_digit_suffix(self):
        assert (
            _norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2941669")
            == "https://mujrozhlas.cz/hra/osada"
        )

    def test_keeps_short_numeric_suffix(self):
        # Short numbers are legitimate (e.g. "-2" part numbering), only 7+ digits are re-air IDs
        assert (
            _norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2")
            == "https://mujrozhlas.cz/hra/osada-2"
        )


class TestNormTitle:
    def test_strips_diacritics_and_lowercases(self):
        assert _norm_title("Bílá Nemoc") == "bila nemoc"

    def test_strips_series_prefix(self):
        assert _norm_title("Osada: dil prvni", series_prefix="Osada") == "dil prvni"

    def test_none_is_empty(self):
        assert _norm_title(None) == ""


class TestDedupeDiscovered:
    def test_tier1_ext_id_match(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Osada 1", ext_id="uuid-1"),
            FakeEntry(url="https://b.cz/other", title="Different", ext_id="uuid-1"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "ext_id"

    def test_tier2_reair_url_match(self):
        entries = [
            FakeEntry(url="https://a.cz/hra/osada-2941669", title="Osada"),
            FakeEntry(url="https://a.cz/hra/osada-3000001", title="totally different title"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "url_reair"

    def test_tier3_fuzzy_title_match(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Bila nemoc, cast prvni"),
            FakeEntry(url="https://b.cz/2", title="Bílá nemoc, část první"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "title_fuzzy"

    def test_generic_titles_never_fuzzy_matched(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Epizody pořadu"),
            FakeEntry(url="https://b.cz/2", title="Epizody pořadu"),
        ]
        unique, _groups = dedupe_discovered(entries)
        # Same generic title on different URLs must NOT collapse
        assert len(unique) == 2

    def test_existing_db_episode_blocks_reimport(self):
        entries = [FakeEntry(url="https://a.cz/hra/osada", title="Osada")]
        existing = [FakeEntry(url="https://a.cz/hra/osada", title="Osada", ext_id="uuid-9")]
        unique, groups = dedupe_discovered(entries, existing_episodes=existing)
        assert len(unique) == 0
        assert groups[0].canonical_url == "(existing in DB)"

    def test_distinct_entries_all_kept(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Osada, cast prvni"),
            FakeEntry(url="https://a.cz/2", title="Zahrada, cast druha"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 2
        assert groups == []
