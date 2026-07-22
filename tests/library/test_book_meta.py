"""Radio-title normalization (user rules, works/113 case)."""
from audiobiblio.library.book_meta import (
    BookMeta, default_genre, parse_book_title, year_from_description,
)

RAW = ("Petr Stančík: Karel je king. Mýty, omyly a pikantnosti "
       "ze života Karla IV. Čte Vojta Dyk")


def test_karel_je_king_decomposition():
    m = parse_book_title(RAW)
    assert m.author == "Petr Stancik"
    assert m.title == "Karel je king"
    assert m.subtitle == "Mýty, omyly a pikantnosti ze života Karla IV"
    assert m.narrator == "Vojta Dyk"


def test_no_author_no_narrator():
    m = parse_book_title("Zlatý poklad republiky. Kam zmizely rezervy")
    assert m.author is None
    assert m.title == "Zlaty poklad republiky"
    assert m.narrator is None


def test_ctou_variant_and_unidecode():
    m = parse_book_title("Paula Hawkins: Dívka ve vlaku. Čtou Anita Krausová a Jan Novák")
    assert m.author == "Paula Hawkins"
    assert m.title == "Divka ve vlaku"
    assert m.narrator == "Anita Krausova a Jan Novak"


def test_year_clue_beats_broadcast():
    d = "… Natočeno v roce 2016 u příležitosti 700. výročí …"
    assert year_from_description(d) == 2016
    assert year_from_description("bez indicie") is None


def test_default_genre():
    assert default_genre("Četba na pokračování") == "audiokniha; cetba na pokracovani"


def test_stem_truncation_never_eats_part_number():
    """24 parts of a long-titled book collapsed to ONE filename — truncation
    must sacrifice the work prefix, never the part discriminator."""
    from types import SimpleNamespace
    from audiobiblio.library.pipelines.library import build_paths_for_episode

    long_album = ("Petr Stancik Karel je king. Myty, omyly a pikantnosti "
                  "ze zivota Karla IV. Cte Vojta Dyk")
    stems = set()
    for n in (1, 2, 24):
        ep = SimpleNamespace(title=long_album, episode_number=n,
                             work=None, published_at=None)
        work = SimpleNamespace(author="Petr Stancik", year=2026,
                               title=long_album, series=None)
        p = build_paths_for_episode(ep, work=work)
        assert f"{n:02d}" in p["stem"], p["stem"]
        assert len(p["stem"]) <= 80
        stems.add(p["stem"])
    assert len(stems) == 3, "each part must have a distinct filename"
