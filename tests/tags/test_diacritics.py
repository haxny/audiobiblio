"""Characterization tests for audiobiblio.tags.diacritics.

These pin down EXISTING behavior before the module restructure.
If one fails, the code has a real bug — report it, do not adjust the test.
"""
from audiobiblio.tags.diacritics import (
    apply_czech_parts_replacement,
    detect_czech_content,
    fix_windows1250,
    strip_diacritics,
)


class TestStripDiacritics:
    def test_czech_lowercase(self):
        assert strip_diacritics("příliš žluťoučký kůň") == "prilis zlutoucky kun"

    def test_czech_uppercase(self):
        assert strip_diacritics("ŘEŘICHA ŽÍŽALA") == "RERICHA ZIZALA"

    def test_ascii_passthrough(self):
        assert strip_diacritics("Karel Capek") == "Karel Capek"

    def test_empty_string(self):
        assert strip_diacritics("") == ""

    def test_win1250_corruption_also_stripped(self):
        # 'ø' is a corrupted 'ř' in Win-1250-as-Latin-1 tags
        assert strip_diacritics("Døevo") == "Drevo"


class TestFixWindows1250:
    def test_clean_text_unchanged(self):
        assert fix_windows1250("Bílá nemoc") == "Bílá nemoc"

    def test_empty_unchanged(self):
        assert fix_windows1250("") == ""

    def test_marker_triggers_recode(self):
        # Text containing a Win-1250 marker gets re-decoded; result must
        # differ from input and not raise.
        corrupted = "høbitov"
        fixed = fix_windows1250(corrupted)
        assert fixed != corrupted


class TestDetectCzechContent:
    def test_czech_chars_in_folder(self):
        assert detect_czech_content("Povídky Čapek", []) is True

    def test_czech_chars_in_filename(self):
        assert detect_czech_content("Books", ["01 příběh.mp3"]) is True

    def test_czech_word_in_folder(self):
        assert detect_czech_content("Sedm povidka kapitola", []) is True

    def test_english_content(self):
        assert detect_czech_content("The Hobbit", ["01 Chapter One.mp3"]) is False


class TestCzechPartsReplacement:
    def test_cast_prvni(self):
        assert apply_czech_parts_replacement("Osada, cast prvni") == "Osada-01"

    def test_no_match_unchanged(self):
        assert apply_czech_parts_replacement("Osada") == "Osada"
