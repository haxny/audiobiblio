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
