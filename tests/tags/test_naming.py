"""Characterization tests for audiobiblio.tags.naming (NAMING_CONVENTION.md patterns)."""
from audiobiblio.tags.naming import (
    generate_filename,
    generate_folder_name,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_strips_diacritics(self):
        assert sanitize_filename("Žluťoučký") == "Zlutoucky"

    def test_removes_forbidden_chars(self):
        assert sanitize_filename('A/B\\C:D*E?F"G<H>I|J') == "A-B-CDEFGHIJ"

    def test_collapses_whitespace(self):
        assert sanitize_filename("  Karel   Capek  ") == "Karel Capek"

    def test_empty(self):
        assert sanitize_filename("") == ""


class TestGenerateFilename:
    BASE_TAGS = {
        "albumartist": "Ota Pavel",
        "album": "Sedm deka zlata",
        "date": "1980",
    }

    def test_single_file_with_year(self):
        # Pattern 1: {albumartist} - ({date}) {album}.ext
        name = generate_filename(dict(self.BASE_TAGS), 1, 1, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata.mp3"

    def test_single_file_no_year(self):
        tags = dict(self.BASE_TAGS)
        tags["date"] = ""
        name = generate_filename(tags, 1, 1, ".mp3")
        assert name == "Ota Pavel - Sedm deka zlata.mp3"

    def test_multitrack_no_title(self):
        # Pattern 2: ... - {track}.ext, zero-padded
        name = generate_filename(dict(self.BASE_TAGS), 3, 10, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata - 03.mp3"

    def test_multitrack_with_title(self):
        # Pattern 3: ... - {track} {title}.ext
        tags = dict(self.BASE_TAGS, title="Zlate uhori")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata - 01 Zlate uhori.mp3"

    def test_tracknumber_tag_overrides_index(self):
        tags = dict(self.BASE_TAGS, tracknumber="7")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert " - 07.mp3" in name

    def test_tracknumber_with_total_uses_number_part(self):
        # "7/12" must yield 07, not crash (plain numbers rule)
        tags = dict(self.BASE_TAGS, tracknumber="7/12")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert " - 07.mp3" in name

    def test_disc_number_prefixes_track(self):
        # Pattern 6: disc 2 track 3 -> 203
        tags = dict(self.BASE_TAGS, discnumber="2", title="Kapitola")
        name = generate_filename(tags, 3, 20, ".mp3")
        assert " - 203 Kapitola.mp3" in name

    def test_long_title_truncated_to_filesystem_limit(self):
        tags = dict(self.BASE_TAGS, title="x" * 300)
        name = generate_filename(tags, 1, 10, ".mp3")
        assert len(name) <= 250


class TestGenerateFolderName:
    def test_with_year(self):
        tags = {"albumartist": "Ota Pavel", "album": "Sedm deka zlata", "date": "1980-05-01"}
        assert generate_folder_name(tags) == "Ota Pavel - (1980) Sedm deka zlata"

    def test_without_year(self):
        tags = {"albumartist": "Ota Pavel", "album": "Sedm deka zlata"}
        assert generate_folder_name(tags) == "Ota Pavel - Sedm deka zlata"

    def test_falls_back_to_artist(self):
        tags = {"artist": "Ota Pavel", "album": "Sedm deka zlata"}
        assert generate_folder_name(tags) == "Ota Pavel - Sedm deka zlata"
