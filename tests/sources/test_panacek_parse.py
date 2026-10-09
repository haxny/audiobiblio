"""Parsing mluvenypanacek.cz records into structured data (real record texts)."""
from __future__ import annotations

from audiobiblio.sources.panacek_parse import parse_record


def _rec(title, lines, cats=(3,), link="https://mluvenypanacek.cz/x/1-x.html"):
    html = "".join(f"<p>{l}</p>" for l in lines)
    return {"id": 1, "link": link, "categories": list(cats), "title": {"rendered": title},
            "content": {"rendered": html}, "date": "2009-01-01T00:00:00", "modified": "2020-01-01T00:00:00"}


def test_reading_with_parts_and_lengths():
    r = parse_record(_rec("Kráčel po nestejně napjatých lanech 1/5 (2007)", [
        "Olga Walló. Pětidílná četba na pokračování z autobiografického románu. Pro rozhlas vybral Petr Hanuška. "
        "Redakce Jan Sulovský. Hudební spolupráce Antonín Schindler. Rozhlasová úprava a režie Michal Bureš.",
        "Účinkuje Olga Kaštická.",
        "Natočeno ve studiu Olomouc v roce 2007. (díly 28, 31, 30, 26, 29 min., celkem 2:23 hod.). "
        "Premiéra ve dnech 4., 11., 18., 25. 11. a 2. 12. 2007 od 20:30 hod. na stanici ČRo 5 Olomouc v cyklu Setkání s literaturou."]))
    assert r["title_base"] == "Kráčel po nestejně napjatých lanech"
    assert (r["part"], r["parts_total"]) == (1, 5)
    assert r["authors"] == ["Olga Walló"]
    assert r["narrators"] == ["Olga Kaštická"]
    assert r["credits"]["rezie"] == "Michal Bureš"
    assert r["recorded_years"] == [2007]
    assert r["part_minutes"] == [28, 31, 30, 26, 29]
    assert r["total_minutes"] == 143


def test_story_with_reader_and_translation():
    r = parse_record(_rec("Hamail (1993)", [
        "Karel May. Četba na pokračování vás tentokrát zavede mezi beduíny. Překlad Josef Ladislav Turnovský. "
        "Rozhlasová úprava Eduard Kára. Režie Vladimír Gromov.",
        "Čte Bohuslav Kalva.",
        "Natočeno 11. 10. 1993 (12 min.). Premiéra (?) 30. 10. 1995."], cats=(5,)))
    assert r["authors"] == ["Karel May"]
    assert r["narrators"] == ["Bohuslav Kalva"]
    assert r["credits"]["preklad"] == "Josef Ladislav Turnovský"
    assert r["recorded_years"] == [1993]
    assert r["total_minutes"] == 12
    assert r["category"] == "povidky"


def test_cast_with_roles_and_cd_length():
    r = parse_record(_rec("Staré pověsti české 1/38 (1990-1991, 2011)", [
        "Miloslav Steiner a Jiří Černý. Zvuková spolupráce Josef Plechatý. Režie Mária Křepelková.",
        "Osoby a obsazení: vypravěč (Eduard Cupák), Jiří Holý, hrabě Kroll (Jiří Schwarz), Gréta (Martina Hudečková).",
        "Natočeno 30. 11. 2009 (1 – 12), 21. 2. 1991 (13 – 15).",
        "Vydala společnost CODI Art & Production Agency 2., 16. a 30. listopadu 2010 (3 CD, 2:59:36)."], cats=(13, 4)))
    assert r["authors"] == ["Miloslav Steiner", "Jiří Černý"]
    assert {"role": "vypravěč", "actor": "Eduard Cupák"} in r["cast"]
    assert {"role": "hrabě Kroll", "actor": "Jiří Schwarz"} in r["cast"]
    assert {"role": None, "actor": "Jiří Holý"} in r["cast"]
    assert r["narrators"] == ["Eduard Cupák"]          # the vypravěč reads
    assert r["parts_total"] == 38
    assert sorted(r["recorded_years"]) == [1991, 2009]
    assert r["release_minutes"] == 180                 # 2:59:36 rounded


def test_parts_list_and_kids_series():
    r = parse_record(_rec("Bobulka a Lísteček 1/4 (1979-1980, 2023)", [
        "Helena Philippová (1-2) a Alena Riegerová (3-4). Pohádkový seriál. Režie Helena Philippová.",
        "Osoby a obsazení: vypravěč, chlapec Jakub (Zdeněk Řehoř), Bobulka (Vlastimil Brodský) a Lísteček (Jiřina Bohdalová).",
        "Připravil Československý rozhlas v roce 1979 (1-2) a 1980 (3-4) (4 x 15 min.).",
        "Obsah: 1. Jak Bobulka s Lístečkem závodili v běhu – 2. Jak Bobulka s Lístečkem soutěžili o zlatou šišku "
        "– 3. Jak Bobulka s Lístečkem pěstovali turistiku – 4. Jak se Bobulka s Lístečkem naučili plavat."], cats=(24,)))
    assert r["authors"] == ["Helena Philippová", "Alena Riegerová"]
    assert r["narrators"] == ["Zdeněk Řehoř"]
    assert r["recorded_years"] == [1979, 1980]
    assert r["part_minutes"] == [15, 15, 15, 15]
    assert r["parts"][0] == "Jak Bobulka s Lístečkem závodili v běhu"
    assert len(r["parts"]) == 4


def test_play_with_members_of_ensemble():
    r = parse_record(_rec("Nezabudni! (Nezapomeň!, 1947)", [
        "Ferdinand Peroutka (divadelní hra Oblak a valčík). Rozhlasová úprava a režie Jozef Budský.",
        "Účinkují členové ND.",
        "Nastudovala Bratislava v roce 1947. Premiéra 28. 10. 1947 (Bratislava, 21:00 – 22:00 h.)."], cats=(2,)))
    assert r["authors"] == ["Ferdinand Peroutka"]
    assert r["alt_title"] == "Nezapomeň!"
    assert r["recorded_years"] == [1947]
    assert r["cast"] == []                              # "členové ND" is not a person
