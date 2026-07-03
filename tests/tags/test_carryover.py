"""Tests for audiobiblio.tags.carryover — old file's curated tags survive replacement.

TDD: these tests were written before the implementation.

Fixtures:
- ``silent_m4a_factory``: creates named silent M4A files (from fixtures_util).
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Callable

import pytest

from audiobiblio.tags.carryover import ALL_KNOWN_FIELDS, carry_over_tags
from audiobiblio.tags.reader import read_tags
from audiobiblio.tags.writer import write_tags


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers: build the old / new files with distinct tag sets
# ---------------------------------------------------------------------------


def _old_file(path: Path) -> None:
    """Tag the 'old' file with curated values (simulates human-edited tags)."""
    write_tags(
        path,
        album_tags={
            "album": "Stara Kniha",
            "albumartist": "Stary Autor",
            "artist": "Stary Autor",
            "genre": "Roman; Historicky",
            "date": "1990",
            "publisher": "Stare Nakladatelstvi",
            "comment": "Komentar ze stareho souboru",
        },
        track_tags={"title": "Stara kapitola", "tracknumber": "3"},
    )


def _new_file(path: Path) -> None:
    """Tag the 'new' file with fresh-download defaults (may be incomplete)."""
    write_tags(
        path,
        album_tags={
            "album": "Nova Kniha",
            "albumartist": "Novy Autor",
            "artist": "Novy Autor",
            "genre": "Audiokniha",
            "date": "2024",
            "publisher": "Nove Nakladatelstvi",
        },
        track_tags={"title": "Nova kapitola", "tracknumber": "1"},
    )


# ---------------------------------------------------------------------------
# Test 1: old values overwrite new file's defaults for protected fields
# ---------------------------------------------------------------------------


def test_old_values_win(silent_m4a_factory: Callable[[str], Path]) -> None:
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    _old_file(old_path)
    _new_file(new_path)

    carry_over_tags(old_path, new_path)

    tags = read_tags(str(new_path))

    assert tags.get("album") == "Stara Kniha", "album should come from old file"
    assert tags.get("albumartist") == "Stary Autor", "albumartist should come from old file"
    assert tags.get("artist") == "Stary Autor", "artist should come from old file"
    assert tags.get("date") == "1990", "date should come from old file"
    assert tags.get("publisher") == "Stare Nakladatelstvi", "publisher should come from old file"
    assert tags.get("title") == "Stara kapitola", "title should come from old file"
    assert tags.get("tracknumber") == "3", "tracknumber should come from old file"
    # Genre: the writer stores as freeform split on ';'; reader joins with '; '
    genre = tags.get("genre", "")
    assert "Roman" in genre, f"genre should contain 'Roman', got {genre!r}"


# ---------------------------------------------------------------------------
# Test 2: empty old fields do NOT blank new file's existing values
# ---------------------------------------------------------------------------


def test_empty_old_does_not_blank_new(silent_m4a_factory: Callable[[str], Path]) -> None:
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    # Old file has NO performer tag (empty)
    write_tags(
        old_path,
        album_tags={"album": "Stara Kniha", "artist": "Stary Autor"},
        track_tags={"title": "Stara kapitola"},
    )
    # New file has a performer
    write_tags(
        new_path,
        album_tags={"album": "Nova Kniha", "artist": "Novy Autor", "performer": "Ctenar Nova"},
        track_tags={"title": "Nova kapitola"},
    )

    carry_over_tags(old_path, new_path)

    tags = read_tags(str(new_path))
    # performer was absent in old → new file's performer must survive
    assert tags.get("performer") == "Ctenar Nova", (
        "performer should not be blanked when old has no performer"
    )
    # album came from old (non-empty)
    assert tags.get("album") == "Stara Kniha"


# ---------------------------------------------------------------------------
# Test 3: return dict contains exactly the fields that were written
# ---------------------------------------------------------------------------


def test_returns_dict_of_written_fields(silent_m4a_factory: Callable[[str], Path]) -> None:
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    write_tags(
        old_path,
        album_tags={"album": "Stara Kniha", "artist": "Stary Autor"},
        track_tags={"title": "Stara kapitola"},
    )
    write_tags(new_path, album_tags={}, track_tags={})

    written = carry_over_tags(old_path, new_path)

    # All returned keys must be non-empty field names
    assert isinstance(written, dict)
    assert len(written) > 0
    # Keys that were non-empty in old file must be present
    assert "album" in written
    assert "artist" in written
    assert "title" in written
    # Values in returned dict must match what was in the old file
    assert written["album"] == "Stara Kniha"
    assert written["title"] == "Stara kapitola"


# ---------------------------------------------------------------------------
# Test 4: old file is byte-identical after carry_over (never touched)
# ---------------------------------------------------------------------------


def test_old_file_byte_identical(silent_m4a_factory: Callable[[str], Path]) -> None:
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    _old_file(old_path)
    _new_file(new_path)

    hash_before = _sha256(old_path)
    carry_over_tags(old_path, new_path)
    hash_after = _sha256(old_path)

    assert hash_before == hash_after, "carry_over_tags must not modify the old file"


# ---------------------------------------------------------------------------
# Test 5: ALL_KNOWN_FIELDS constant covers all writer-supported fields
# ---------------------------------------------------------------------------


def test_all_known_fields_constant() -> None:
    """ALL_KNOWN_FIELDS must include every field the writer supports."""
    expected = {
        "title", "artist", "albumartist", "album", "genre", "date",
        "publisher", "performer", "comment", "www", "tracknumber",
        "translator", "discnumber", "description",
    }
    assert set(ALL_KNOWN_FIELDS) == expected


# ---------------------------------------------------------------------------
# Test 6: selective protect — only requested fields are carried over
# ---------------------------------------------------------------------------


def test_selective_protect(silent_m4a_factory: Callable[[str], Path]) -> None:
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    _old_file(old_path)
    _new_file(new_path)

    # Only carry over album; leave artist, title etc. as-is on new file
    written = carry_over_tags(old_path, new_path, protect=["album"])

    assert set(written.keys()) == {"album"}
    tags = read_tags(str(new_path))
    assert tags.get("album") == "Stara Kniha"
    # artist was NOT in protect → new file retains its artist
    assert tags.get("artist") == "Novy Autor"


# ---------------------------------------------------------------------------
# Test 7: "n/a" in old file is treated as empty (regression test)
# ---------------------------------------------------------------------------


def test_n_a_treated_as_empty(silent_m4a_factory: Callable[[str], Path]) -> None:
    """Old file with performer="n/a" should not overwrite new file's valid performer."""
    old_path = silent_m4a_factory("old.m4a")
    new_path = silent_m4a_factory("new.m4a")

    # Old file has performer="n/a" (legacy placeholder)
    write_tags(
        old_path,
        album_tags={"album": "Stara Kniha", "artist": "Stary Autor", "performer": "n/a"},
        track_tags={"title": "Stara kapitola"},
    )
    # New file has a real performer value
    write_tags(
        new_path,
        album_tags={"album": "Nova Kniha", "artist": "Novy Autor", "performer": "Ctenar Nova"},
        track_tags={"title": "Nova kapitola"},
    )

    written = carry_over_tags(old_path, new_path)

    tags = read_tags(str(new_path))
    # performer="n/a" in old file must be ignored; new file's performer must survive
    assert tags.get("performer") == "Ctenar Nova", (
        'performer="n/a" should be treated as empty and not overwrite valid new value'
    )
    # "performer" should NOT be in written dict since old value was treated as empty
    assert "performer" not in written, (
        "performer should not be in written dict when old value is n/a"
    )
