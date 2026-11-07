"""Download file names must not repeat the author (2026-11-07, live:
'Adam Georgiev - Adam Georgiev Denik sestry Lidy Baarove - 01.m4a')."""
from __future__ import annotations

from types import SimpleNamespace

from audiobiblio.library.pipelines.library import build_paths_for_episode


def _stem(title, author, n=1, ep_title=None):
    ep = SimpleNamespace(title=ep_title or title, episode_number=n,
                         work=None, published_at=None)
    work = SimpleNamespace(author=author, year=None, title=title, series=None)
    return build_paths_for_episode(ep, work=work)["stem"]


def test_author_prefix_is_not_repeated_in_stem():
    s = _stem("Adam Georgiev: Deník sestry Lídy Baarové", "Adam Georgiev")
    assert s == "Adam Georgiev - Denik sestry Lidy Baarove - 01", s


def test_teaser_sentence_dropped_from_album():
    s = _stem("Anna Bolavá: Ke dnu. Mrazivý příběh z maloměsta", "Anna Bolavá", n=3)
    assert s == "Anna Bolava - Ke dnu - 03", s


def test_title_without_author_prefix_kept():
    s = _stem("Ke dnu", "Anna Bolavá", n=2)
    assert s == "Anna Bolava - Ke dnu - 02", s


def test_redaktor_author_does_not_strip_real_writer():
    # 'Karel Čapek:' does not name the byline author → title kept whole
    s = _stem("Karel Čapek: Povídky z jedné kapsy", "Petra Hynčíková")
    assert "Karel Capek" in s, s
