"""write_genre() changes ONLY the genre and verifies the file afterwards."""
from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen.id3 import ID3, TCOM, TXXX
from mutagen.mp4 import MP4, MP4FreeForm

from audiobiblio.tags.writer import read_genre, write_genre, write_tags

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _audio(tmp_path, ext, tagged=True):
    out = tmp_path / f"a.{ext}"
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", "-y"]
    if not tagged:
        cmd += ["-write_id3v2", "0", "-map_metadata", "-1"]
    subprocess.run(cmd + [str(out)], check=True)
    return out


def test_mp3_only_genre_changes(tmp_path):
    p = _audio(tmp_path, "mp3")
    id3 = ID3()
    id3.add(TCOM(encoding=3, text="Hudba: Petr Mandel"))
    id3.add(TXXX(encoding=1, desc="Performer", text="Jiri Schwarz"))
    id3.save(p, v2_version=3)
    write_genre(p, ["audiokniha", "detektivka"])
    after = ID3(p)
    assert after["TCON"].text == ["audiokniha; detektivka"]
    assert after["TCOM"].text == ["Hudba: Petr Mandel"]          # composer survives
    assert after["TXXX:Performer"].text == ["Jiri Schwarz"]
    assert read_genre(p) == ["audiokniha", "detektivka"]


def test_mp3_without_any_tag_header(tmp_path):
    p = _audio(tmp_path, "mp3", tagged=False)
    write_genre(p, ["audiokniha"])
    assert read_genre(p) == ["audiokniha"]


def test_m4a_writes_freeform_genre_and_drops_standard_atom(tmp_path):
    p = _audio(tmp_path, "m4a")
    mp4 = MP4(p)
    mp4["\xa9gen"] = ["Speech"]
    mp4["\xa9ART"] = ["Karel Capek"]
    mp4["----:com.apple.iTunes:PERFORMER"] = [MP4FreeForm(b"Jiri Schwarz")]
    mp4.save()
    write_genre(p, ["audiokniha", "Vecerni drama (CRo3)"])
    after = MP4(p)
    assert "\xa9gen" not in after
    assert [bytes(v).decode() for v in after["----:com.apple.iTunes:GENRE"]] == [
        "audiokniha", "Vecerni drama (CRo3)"]
    assert after["\xa9ART"] == ["Karel Capek"]
    assert bytes(after["----:com.apple.iTunes:PERFORMER"][0]) == b"Jiri Schwarz"


def test_flac_genre(tmp_path):
    p = _audio(tmp_path, "flac")
    write_genre(p, ["audiokniha", "humor"])
    assert read_genre(p) == ["audiokniha", "humor"]


def test_empty_list_removes_genre(tmp_path):
    p = _audio(tmp_path, "m4a")
    write_genre(p, ["audiokniha"])
    write_genre(p, [])
    assert read_genre(p) == []


def test_write_tags_on_mp3_without_header_does_not_crash(tmp_path):
    # nightly sync failed with "Missing filename or fileobj argument" (2026-10-31)
    p = _audio(tmp_path, "mp3", tagged=False)
    write_tags(p, {"album": "Krakatit", "genre": "audiokniha"}, {"title": "01"})
    assert read_genre(p) == ["audiokniha"]
