"""Genre clean-up: map genre atoms through the user-approved table (2026-11-01)."""
from __future__ import annotations

from audiobiblio.tags.genre_canon import apply_genre_map, genre_key, split_genres

MAP = {
    "audiokniha": "audiokniha",
    "blues": None,
    "detektivky": "detektivka",
    "mluvene slovo - detektivka": "detektivka",
    "literatura ceska": "ceska literatura",
    "dejiny, historie": "dejiny; historie",
}


def test_genre_key_ignores_case_diacritics_and_spacing():
    assert genre_key("  Mluvené   Slovo ") == "mluvene slovo"
    assert genre_key("AUDIOKNÍHA") == "audiokniha"


def test_split_genres_handles_semicolons_and_nul_glue():
    assert split_genres(["audiokniha\x00detektivka", "krimi; humor", " "]) == [
        "audiokniha", "detektivka", "krimi", "humor"]


def test_mapped_atoms_change_dropped_atoms_vanish():
    assert apply_genre_map(["Audiokniha", "Blues", "Detektivky"], MAP) == [
        "audiokniha", "detektivka"]


def test_unknown_atoms_stay_exactly_as_found():
    assert apply_genre_map(["Audiokniha", "Staroveky Egypt"], MAP) == [
        "audiokniha", "Staroveky Egypt"]


def test_target_with_several_genres_is_split_and_deduplicated():
    assert apply_genre_map(["Dejiny, historie", "historie", "mluvene slovo - detektivka",
                            "detektivky"], MAP) == ["dejiny", "historie", "detektivka"]


def test_input_list_is_not_mutated():
    src = ["Blues", "audiokniha"]
    apply_genre_map(src, MAP)
    assert src == ["Blues", "audiokniha"]
