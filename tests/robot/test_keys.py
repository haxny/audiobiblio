"""Matching keys and classification for the librarian robot."""
from __future__ import annotations

from audiobiblio.robot.keys import classify_pair, work_key


def test_key_from_folder_name_variants_is_stable():
    a = work_key(None, None, "Barbora Haplova - (2020) Josef Rosol a Konrad Sliz (blazniva pohadka)")
    b = work_key(None, None, "Barbora Haplova – (2020) Josef Rosol a Konrad Sliz.mp3")
    c = work_key("Barbora Haplová", "Josef Rosol a Konrád Slíž", None)
    assert a == b == c


def test_key_ignores_narrator_and_length_suffixes():
    a = work_key(None, None, "Haplova Barbora - Mechovky a trilobiti. Poutave pribehy z nasich pramori (Ondrej Brousek)2024(1h3m)")
    b = work_key("Barbora Haplova", "Mechovky a trilobiti", None)
    assert a == b


def test_key_without_author_uses_title_only():
    assert work_key(None, "Podvodnici", None) == work_key(None, None, "Podvodnici")


def test_classify_by_length():
    assert classify_pair(3600, 3640) == "same"          # ±2 %
    assert classify_pair(3600, 4200) == "version"
    assert classify_pair(None, 4200) == "unknown"
