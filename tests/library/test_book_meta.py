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


class TestLooksLikePersonNames:
    """Narrator values must be names — 1,252 episodes got sentence fragments
    ('narsky denik' from 'Čtenářský deník', 'o svem detstvi…' from
    'Vypráví o svém dětství') before this guard (2026-10-09)."""

    def test_accepts_names(self):
        from audiobiblio.library.book_meta import looks_like_person_names as ok
        for v in ["Jiri Schwarz", "Jiří Schwarz", "Vojta Dyk", "Eliska Vocelova a Petr Kostka",
                  "Martha Issová, Robert Mikluš", "Jan Werich st.", "Hana Maciuchová"]:
            assert ok(v), v

    def test_rejects_fragments_and_sentences(self):
        from audiobiblio.library.book_meta import looks_like_person_names as ok
        for v in ["narsky denik", "ni na pokracovani", "a komentuje:",
                  "o svem chapani umeni, vcetne noveho cirkusu", "Schwarz",
                  "v dokumentu Vlastimil Dvorak", "", None,
                  "pedagogove Vladimir Kokolia, Petr Dub"]:
            assert not ok(v), v


class TestCleanPersonNames:
    def _c(self, v):
        from audiobiblio.library.book_meta import clean_person_names
        return clean_person_names(v)

    def test_fragments_become_none(self):
        for v in ["narsky denik", "ni na pokracovani", "a komentuje:", "Schwarz",
                  "o svem chapani umeni, vcetne noveho cirkusu",
                  "pedagogove Vladimir Kokolia, Petr Dub", "TLlay\x11(1\x002D", None, ""]:
            assert self._c(v) is None, v

    def test_cast_lists_are_cleaned(self):
        assert self._c("Ales Prochazka (vypravec), Ondrej Maly (Dan), Ales Bilik (Karas) "
                       "a Matous Ruml (Novak)") == \
            "Ales Prochazka, Ondrej Maly, Ales Bilik a Matous Ruml"
        assert self._c("Ales Prochazka | Dvojka") == "Ales Prochazka"
        assert self._c("Ivan Rezac, Apolena Veldova, Jan Holik a dalsi") == \
            "Ivan Rezac, Apolena Veldova, Jan Holik a dalsi"  # valid: unchanged
        assert self._c("Libor Vacek, Marta Zemanova") == "Libor Vacek, Marta Zemanova"
        assert self._c("Lucie Vavrickova ze Statniho oblastniho archivu v Litomericic") == \
            "Lucie Vavrickova"
        assert self._c("Ales Prochazka (vypravec), Ondrej Maly (Dan) a dalsi") == \
            "Ales Prochazka, Ondrej Maly a dalsi"
        assert self._c("Josef PejchalProdukce: Tereza Miciakova a Blanka TunovaNatoceno: 2023") \
            == "Josef Pejchal"
        assert self._c("Jan Hartl a Borivoj Navratil. Poslouchejte on-line po dobu tydne") \
            == "Jan Hartl a Borivoj Navratil"
        assert self._c("Jiri Labus a ornitolog Zdenek Vermouzek") == "Jiri Labus"
        assert self._c("Jiri Schwarz") == "Jiri Schwarz"
        assert self._c("Jan Werich st.") == "Jan Werich st."
