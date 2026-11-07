"""Composer tag (TCOM / ©wrt) — written only when the caller supplies it
(music: live-recorded D-dur blocks); audiobook calls keep the old shape."""
from __future__ import annotations

from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from audiobiblio.tags.writer import write_tags
from tests.fixtures_util import _make_silent_audio


def test_mp3_composer_written_and_absent_by_default(tmp_path):
    p = _make_silent_audio(tmp_path / "a.mp3", ["-c:a", "libmp3lame"])
    write_tags(p, {"album": "A", "composer": "Anton Bruckner"}, {"title": "T"})
    assert str(ID3(p)["TCOM"]) == "Anton Bruckner"
    write_tags(p, {"album": "A"}, {"title": "T"})        # audiobook call: no composer
    assert "TCOM" not in ID3(p)


def test_mp4_composer_only_touched_when_given(silent_m4a):
    write_tags(silent_m4a, {"album": "A", "composer": "Dmitrij Sostakovic"}, {"title": "T"})
    assert MP4(silent_m4a).tags["\xa9wrt"] == ["Dmitrij Sostakovic"]
    write_tags(silent_m4a, {"album": "A"}, {"title": "T"})  # e.g. nightly sync rewrite
    assert MP4(silent_m4a).tags["\xa9wrt"] == ["Dmitrij Sostakovic"]
