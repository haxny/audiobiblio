"""A chapter title that only repeats the album is not written (user rule 2026-10-09)."""
from __future__ import annotations

from audiobiblio.tags import writer
from audiobiblio.tags.writer import title_is_redundant, write_tags


def test_title_is_redundant():
    assert title_is_redundant("Krvavá pavlač", "Krvava pavlac")
    assert title_is_redundant("Krvava pavlac.", "Krvava pavlac")
    assert not title_is_redundant("01 Den pote", "Den trifidu")
    assert not title_is_redundant("", "Krvava pavlac")
    assert not title_is_redundant("Krvava pavlac", None)


def test_write_tags_drops_redundant_title(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(writer, "_write_mp4", lambda p, a, t, c: seen.update(t))
    track = {"title": "Krvava pavlac", "tracknumber": "1"}
    write_tags(tmp_path / "x.m4a", {"album": "Krvava pavlac"}, track)
    assert seen == {"title": "", "tracknumber": "1"}
    assert track["title"] == "Krvava pavlac"          # caller's dict untouched
    write_tags(tmp_path / "x.m4a", {"album": "Den trifidu"}, {"title": "Den pote"})
    assert seen["title"] == "Den pote"
