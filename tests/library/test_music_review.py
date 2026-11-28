"""Music review programs (Kulata muzika): one episode = a review of one album.

Title shapes seen live 2026-11: 'Artist - Album (2017)', 'Artist: Album (1991)',
no year, or free text with no separator. The reviewed ARTIST must become the
artist tag so Plex pairs the review with the artist's own albums.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from audiobiblio.library.music_review import (
    ReviewTitle, parse_review_title, review_stem, review_tags)


@pytest.mark.parametrize("title,expected", [
    ("Jaroslav Olin Nejezchleba - Necekana navsteva (2017)",
     ReviewTitle("Jaroslav Olin Nejezchleba", "Necekana navsteva", 2017)),
    ("Zuzana Navarova: Jako Santidevi (2003)",
     ReviewTitle("Zuzana Navarova", "Jako Santidevi", 2003)),
    ("Wabi Danek: Dablovo stado", ReviewTitle("Wabi Danek", "Dablovo stado", None)),
    ("Epydemye - Kotlina", ReviewTitle("Epydemye", "Kotlina", None)),
    ("Greenhorns - Zelenaci '72 (1972)", ReviewTitle("Greenhorns", "Zelenaci '72", 1972)),
    ("Matej Ptaszek & Dobre rano blues band - Bluesgrass (2014)",
     ReviewTitle("Matej Ptaszek & Dobre rano blues band", "Bluesgrass", 2014)),
])
def test_parse_review_title(title, expected):
    assert parse_review_title(title) == expected


@pytest.mark.parametrize("title", ["Jan Spaleny v udoli vzpominek", "", None,
                                   "Vladimir Misik and etc..."])
def test_free_text_is_not_guessed(title):
    assert parse_review_title(title) is None


def _ep(title, when=datetime(2026, 11, 18, 17, 30)):
    return SimpleNamespace(title=title, published_at=when, episode_number=None)


def test_stem_has_air_date_artist_album_year():
    assert review_stem(_ep("Jaroslav Olin Nejezchleba - Necekana navsteva (2017)")) == \
        "20261118 Jaroslav Olin Nejezchleba - Necekana navsteva (2017)"


def test_stem_of_free_text_title_keeps_title_after_date():
    assert review_stem(_ep("Jan Spaleny v udoli vzpominek")) == \
        "20261118 Jan Spaleny v udoli vzpominek"


def test_tags_pair_the_review_with_the_artist():
    album, track = review_tags(_ep("Zuzana Navarova: Jako Santidevi (2003)"),
                               program_name="Kulatá muzika", genre="Folk")
    assert album["artist"] == album["albumartist"] == "Zuzana Navarova"
    assert album["album"] == "Kulata muzika"
    assert album["date"] == "2026-11-18"
    assert track["title"] == "Jako Santidevi (2003)"
    assert track["tracknumber"] == ""


def test_free_text_title_gets_no_artist():
    album, track = review_tags(_ep("Jan Spaleny v udoli vzpominek"),
                               program_name="Kulatá muzika", genre="")
    assert album["artist"] == "" and album["albumartist"] == ""
    assert track["title"] == "Jan Spaleny v udoli vzpominek"


# --- wiring: paths and tags go through the review profile ----------------

def _kulata_episode(db_session, episode_factory, content_class="MUSIC-OTHER"):
    from audiobiblio.core.db.models import CrawlTarget
    ep = episode_factory("Kulatá muzika")
    prog = ep.work.series.program
    prog.url = "https://dvojka.rozhlas.cz/kulata-muzika-7046813"
    prog.genre = "Folk"
    ep.title = "Zuzana Navarova: Jako Santidevi (2003)"
    ep.published_at = datetime(2026, 11, 18, 17, 30)
    db_session.add(CrawlTarget(url=prog.url, kind="program", content_class=content_class))
    db_session.flush()
    return ep


def test_paths_go_to_the_music_share_with_review_stem(db_session, episode_factory):
    from audiobiblio.library.pipelines.library import build_paths_for_episode
    ep = _kulata_episode(db_session, episode_factory)
    p = build_paths_for_episode(ep, work=ep.work)
    assert str(p["base_dir"]) == "/media/music/mujrozhlas.cz/Kulata muzika (tst)"
    assert p["stem"] == "20261118 Zuzana Navarova - Jako Santidevi (2003)"


def test_audio_tags_written_for_review(db_session, episode_factory, monkeypatch, tmp_path):
    from audiobiblio.library.pipelines import postprocess
    ep = _kulata_episode(db_session, episode_factory)
    seen = {}
    monkeypatch.setattr(postprocess, "write_tags",
                        lambda path, album, track: seen.update(album=album, track=track))
    postprocess.tag_audio(tmp_path / "x.m4a", ep, ep.work, force=True)
    assert seen["album"]["artist"] == "Zuzana Navarova"
    assert seen["album"]["album"] == "Kulata muzika"
    assert seen["track"]["title"] == "Jako Santidevi (2003)"


def test_non_music_target_keeps_audiobook_naming(db_session, episode_factory):
    from audiobiblio.library.pipelines.library import build_paths_for_episode
    ep = _kulata_episode(db_session, episode_factory, content_class="BOOK")
    p = build_paths_for_episode(ep, work=ep.work)
    assert "/media/music" not in str(p["base_dir"])
