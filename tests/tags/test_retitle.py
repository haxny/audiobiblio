"""write_titles() changes ONLY album / title / track number and verifies the file.

Live 2026-11-27: 'Josef Uher - Albina' carried album 'Josef Uher: Albina. O krehkem …',
the same long text as track title and track number 1 on a single-file story.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen.mp4 import MP4

from audiobiblio.library.retitle_single import clean_tag_title
from audiobiblio.tags.retitle import read_titles, write_titles

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _m4a(tmp_path):
    out = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", "-y", str(out)],
                   check=True)
    m = MP4(out)
    m["\xa9alb"] = ["Josef Uher: Albina. O krehkem prechodu"]
    m["\xa9nam"] = ["Albina. O krehkem prechodu"]
    m["\xa9ART"] = ["Josef Uher"]
    m["\xa9cmt"] = ["x" * 300]
    m["trkn"] = [(1, 0)]
    m.save()
    return out


def test_clean_tag_title_drops_prefix_teaser_and_diacritics():
    assert clean_tag_title("Josef Uher: Albína. O křehkém přechodu", "Josef Uher") == "Albina"
    assert clean_tag_title("Josef Uher Albina. O krehkem", "Josef Uher") == "Albina"


def test_only_titles_change_and_track_is_removed(tmp_path):
    p = _m4a(tmp_path)
    write_titles(p, album="Albina", title=None, track=None)
    t = read_titles(p)
    assert t == {"album": "Albina", "title": None, "track": None}
    m = MP4(p)
    assert m["\xa9ART"] == ["Josef Uher"] and m["\xa9cmt"] == ["x" * 300]


def test_track_can_be_set(tmp_path):
    p = _m4a(tmp_path)
    write_titles(p, album="A", title="B", track=3)
    assert read_titles(p)["track"] == 3


def test_decide_only_when_sure():
    from audiobiblio.library.retitle_single import decide
    cur = {"album": "Josef Uher: Albina. O krehkem", "title": "Albina. O krehkem", "track": 1}
    new, why = decide(cur, "Josef Uher", "x", "Josef Uher", "Albina")
    assert new == {"album": "Albina", "title": None, "track": None} and why == "ok"
    assert decide(cur, "Someone Else", "x", "Josef Uher", "Albina")[0] is None
    assert decide(cur, "Josef Uher", "x", "Josef Uher", "Totally Different")[0] is None
    assert decide({"album": "Albina", "title": None, "track": None},
                  "Josef Uher", "x", "Josef Uher", "Albina")[1] == "already clean"
