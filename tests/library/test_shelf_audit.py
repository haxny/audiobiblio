"""Shelf metadata audit: swapped author/narrator, broken diacritics, mojibake."""
from __future__ import annotations

import json
import sqlite3

from audiobiblio.library.shelf_audit import apply_finding, audit_folder, fix_mojibake, run_shelf_audit
from audiobiblio.sources.panacek_index import build_index


def _post(pid, title, lines, cats=(3,)):
    return {"id": pid, "link": f"https://mluvenypanacek.cz/x/{pid}.html", "categories": list(cats),
            "title": {"rendered": title}, "content": {"rendered": "".join(f"<p>{l}</p>" for l in lines)},
            "date": "2020", "modified": "2020"}


def _index(tmp_path, posts=()):
    d = tmp_path / "dump"
    d.mkdir()
    (d / "posts.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in posts))
    build_index(d, tmp_path / "p.sqlite3")
    return sqlite3.connect(tmp_path / "p.sqlite3")


GORIOT = _post(7, "Otec Goriot (1974)", [
    "Honoré de Balzac. Rozhlasová dramatizace.",
    "Osoby a obsazení: Otec Goriot (Martin Růžek), Anastazie (Alena Vránová), Vauquerová (Marie Vášová).",
    "Natočeno 1974."])

SWAPPED = {"title": "Otec Goriot",
           "authors": ["Martin Růžek, Alena Vránová, Marie Vášová a další"],
           "narrators": ["Honoré de Balzac"],
           "genres": ["Rozhlasová dramatizace"]}


def _folder(tmp_path, author_dir, book_dir, meta):
    folder = tmp_path / "fiction" / author_dir / book_dir
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False))
    return folder


def test_swap_confirmed_by_catalog(tmp_path):
    con = _index(tmp_path, [GORIOT])
    folder = _folder(tmp_path, "Ruzne [audio]", "Otec Goriot", SWAPPED)
    f = audit_folder(folder, SWAPPED, con)
    assert f.verdict == "swapped" and f.confident
    assert f.proposed["authors"] == ["Honoré de Balzac"]
    assert f.proposed["narrators"] == ["Martin Růžek", "Alena Vránová", "Marie Vášová"]
    assert f.link == "https://mluvenypanacek.cz/x/7.html"


def test_swap_confirmed_by_author_folder_without_catalog(tmp_path):
    con = _index(tmp_path)
    folder = _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", SWAPPED)
    f = audit_folder(folder, SWAPPED, con)
    assert f.verdict == "swapped" and f.confident
    assert f.proposed["authors"] == ["Honoré de Balzac"]


def test_correct_roles_are_left_alone(tmp_path):
    con = _index(tmp_path, [GORIOT])
    meta = {"title": "Otec Goriot", "authors": ["Honoré de Balzac"],
            "narrators": ["Martin Růžek", "Alena Vránová"]}
    folder = _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "ok" and f.proposed == {}


def test_wrong_diacritics_take_the_catalog_spelling(tmp_path):
    con = _index(tmp_path, [_post(9, "Řbitov zvířátek (2020)",
                                  ["Stephen King. Román.", "Čte Jan Vlasák.", "Natočeno 2020."])])
    meta = {"title": "Řbitov zviřátek", "authors": ["Stephen King"], "narrators": ["Jan Vlasák"]}
    folder = _folder(tmp_path, "Stephen King [audio]", "Rbitov zviratek", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "diacritics" and f.confident
    assert f.proposed == {"title": "Řbitov zvířátek"}


def test_ascii_names_are_not_a_diacritics_error(tmp_path):
    """ASCII is a deliberate style (file names, tags), not a broken spelling."""
    con = _index(tmp_path, [GORIOT])
    meta = {"title": "Otec Goriot", "authors": ["Honore de Balzac"], "narrators": ["Martin Ruzek"]}
    folder = _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", meta)
    assert audit_folder(folder, meta, con).verdict == "ok"


def test_mojibake_is_repaired_without_catalog(tmp_path):
    assert fix_mojibake("PÅ™Ã­bÄ›h") == "Příběh"
    assert fix_mojibake("Příběh") == "Příběh"
    con = _index(tmp_path)
    meta = {"title": "PÅ™Ã­bÄ›h", "authors": ["Karel ÄŒapek"], "narrators": []}
    folder = _folder(tmp_path, "Karel Capek [audio]", "Pribeh", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "mojibake" and f.confident
    assert f.proposed == {"title": "Příběh", "authors": ["Karel Čapek"]}


def test_unknown_book_is_reported_not_changed(tmp_path):
    con = _index(tmp_path)
    meta = {"title": "Neznámá kniha", "authors": ["Jan Novák"], "narrators": ["Petr Malý"]}
    folder = _folder(tmp_path, "Jan Novak [audio]", "Neznama kniha", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "no_match" and not f.confident and f.proposed == {}


def test_catalog_author_missing_from_metadata_is_only_a_proposal(tmp_path):
    con = _index(tmp_path, [GORIOT])
    meta = {"title": "Otec Goriot", "authors": ["Jan Novák"], "narrators": ["Alena Vránová"]}
    folder = _folder(tmp_path, "Ruzne [audio]", "Otec Goriot", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "roles_conflict" and not f.confident
    assert f.proposed["authors"] == ["Honoré de Balzac"]


def test_apply_merges_and_logs(tmp_path):
    con = _index(tmp_path, [GORIOT])
    folder = _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", SWAPPED)
    f = audit_folder(folder, SWAPPED, con)
    log = tmp_path / "changes.jsonl"
    assert apply_finding(f, log) is True
    written = json.loads((folder / "metadata.json").read_text())
    assert written["authors"] == ["Honoré de Balzac"]
    assert written["genres"] == ["Rozhlasová dramatizace"]          # other keys kept
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["folder"] == str(folder) and entry["verdict"] == "swapped"
    assert entry["before"]["narrators"] == ["Honoré de Balzac"]
    assert apply_finding(f, log) is False                            # idempotent


def test_run_applies_only_confident_findings(tmp_path):
    con = _index(tmp_path, [GORIOT])
    _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", SWAPPED)
    unsure = {"title": "Otec Goriot", "authors": ["Jan Novák"], "narrators": ["Alena Vránová"]}
    _folder(tmp_path, "Ruzne [audio]", "Otec Goriot 2", {**unsure, "title": "Otec Goriot"})
    report, log = tmp_path / "report.jsonl", tmp_path / "changes.jsonl"
    stats = run_shelf_audit(con, [tmp_path / "fiction"], apply=True, report_path=report, log_path=log)
    assert stats == {"swapped": 1, "roles_conflict": 1, "applied": 1}
    assert len(log.read_text().splitlines()) == 1
    rows = [json.loads(l) for l in report.read_text().splitlines()]
    assert {(r["verdict"], r["applied"]) for r in rows} == {("swapped", True), ("roles_conflict", False)}
    dry = run_shelf_audit(con, [tmp_path / "fiction"], apply=False, report_path=report, log_path=log)
    assert dry == {"ok": 1, "roles_conflict": 1}                     # fixed one now reads ok


def test_slash_joined_names_are_split(tmp_path):
    con = _index(tmp_path)
    meta = {"title": "The Second World War", "authors": ["Winston Churchill/Christian Rodska"],
            "narrators": ["Winston Churchill"]}
    folder = _folder(tmp_path, "Winston Leonard Spencer-Churchill [audio]", "WSC", meta)
    assert audit_folder(folder, meta, con).verdict != "swapped"


def test_placeholders_never_become_narrators(tmp_path):
    con = _index(tmp_path)
    meta = {"title": "Ze sveta lesnich samot", "authors": ["Mluvene slovo"],
            "narrators": ["Karel Klostermann"]}
    folder = _folder(tmp_path, "Karel Klostermann [audio]", "Ze sveta", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "swapped"
    assert f.proposed == {"authors": ["Karel Klostermann"], "narrators": []}


def test_narrator_is_catalog_author_is_a_swap_even_without_cast(tmp_path):
    con = _index(tmp_path, [_post(3, "Motýlek (1990)", ["Henri Charrière. Román.", "Natočeno 1990."])])
    meta = {"title": "Motýlek", "authors": ["Bohumil Švarc"], "narrators": ["Henri Charriere"]}
    folder = _folder(tmp_path, "Ruzne [audio]", "Motylek", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "swapped" and f.confident
    assert f.proposed == {"authors": ["Henri Charrière"], "narrators": ["Bohumil Švarc"]}


def test_reader_in_author_field_moves_to_narrators(tmp_path):
    con = _index(tmp_path, [_post(4, "Alláh není povinen (2008)",
                                  ["Ahmadou Kourouma. Román.", "Čte Aleš Procházka.", "Natočeno 2008."])])
    meta = {"title": "Alláh není povinen", "authors": ["Aleš Procházka"], "narrators": None}
    folder = _folder(tmp_path, "Ahmadou Kourouma [audio]", "Allah", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "swapped" and f.confident
    assert f.proposed == {"authors": ["Ahmadou Kourouma"], "narrators": ["Aleš Procházka"]}


def test_name_variants_are_the_same_person(tmp_path):
    con = _index(tmp_path, [
        _post(5, "Konec dětství (1999)", ["Arthur Charles Clarke. Román.", "Čte Václav Neužil.", "Natočeno 1999."]),
        _post(6, "Na Větrné hůrce (2010)", ["Emily Brontëová. Román.", "Čte Petr Vaněk.", "Natočeno 2010."]),
        _post(8, "Vražda v Orient expresu (2000)", ["Agatha Christie. Detektivka.", "Čte Jan Kačer.", "Natočeno 2000."])])
    for title, author, adir in [("Konec detstvi", "A. C. Clarke", "Arthur Charles Clarke"),
                                ("Na Vetrne hurce", "Emily Bronte (Ellis Bell)", "Emily Bronte"),
                                ("Vražda v Orient expresu", "Christie, Agatha", "Agatha Christie")]:
        meta = {"title": title, "authors": [author], "narrators": []}
        folder = _folder(tmp_path, f"{adir} [audio]", title, meta)
        assert audit_folder(folder, meta, con).verdict == "ok", title


def test_upper_case_names_take_the_catalog_spelling(tmp_path):
    con = _index(tmp_path, [_post(9, "Temné hlubiny (2020)",
                                  ["Robert Bryndza. Detektivka.", "Čte Klára Suchá.", "Natočeno 2020."])])
    meta = {"title": "TEMNÉ HLUBINY", "authors": ["ROBERT BRYNDZA"], "narrators": None}
    folder = _folder(tmp_path, "Robert Bryndza [audio]", "Temne hlubiny", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "diacritics" and f.confident
    assert f.proposed == {"title": "Temné hlubiny", "authors": ["Robert Bryndza"]}


def test_title_glued_to_a_name_is_not_a_person(tmp_path):
    con = _index(tmp_path)
    meta = {"title": "První kniha krve", "authors": ["Jiří Zavřel"],
            "narrators": ["Clive Barker - První kniha krve"]}
    folder = _folder(tmp_path, "Clive Barker [audio]", "I. kniha krve", meta)
    assert audit_folder(folder, meta, con).verdict != "swapped"


def test_reader_rule_needs_the_full_name(tmp_path):
    """'Karel Havlíček Borovský' (author field) is not the cast member 'Karel Havlíček'."""
    con = _index(tmp_path, [_post(11, "Přelíčení s občanem Havlíčkem (1990)", [
        "Jiří Horák. Rozhlasová hra.", "Osoby a obsazení: soudce (Karel Havlíček), žalobce (Jan Kačer).",
        "Natočeno 1990."])])
    meta = {"title": "Přelíčení s občanem Havlíčkem", "authors": ["Karel Havlíček Borovský"],
            "narrators": None}
    folder = _folder(tmp_path, "Ruzne [audio]", "Preliceni", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "roles_conflict" and not f.confident


def test_case_or_punctuation_only_differences_are_kept(tmp_path):
    con = _index(tmp_path, [_post(12, "Pilát pontský (1990)", ["Michail Bulgakov. Novela.",
                                                                "Čte Bořivoj Navrátil.", "Natočeno 1990."])])
    meta = {"title": "Pilát Pontský", "authors": ["Michail Bulgakov"], "narrators": None}
    folder = _folder(tmp_path, "Michail Bulgakov [audio]", "Pilat", meta)
    assert audit_folder(folder, meta, con).verdict == "ok"


def test_catalog_and_author_folder_agree_against_the_metadata(tmp_path):
    con = _index(tmp_path, [_post(13, "Zabiják (1990)", ["Ray Bradbury. Povídka.", "Čte Jan Kačer.",
                                                          "Natočeno 1990."])])
    meta = {"title": "Zabiják", "authors": ["Natočeno pro pořad Meteor"], "narrators": None}
    folder = _folder(tmp_path, "Ray Bradbury [audio]", "Ray Bradbury - Zabijak", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict == "wrong_author" and f.confident
    assert f.proposed == {"authors": ["Ray Bradbury"]}


def test_swapped_readers_are_cleaned_to_names(tmp_path):
    con = _index(tmp_path)
    meta = {"title": "Ve znameni Kon-Tiki", "authors": ["(Karel Höeger)1957(1h48m)"],
            "narrators": ["Heyerdahl Thor"]}
    folder = _folder(tmp_path, "Thor Heyerdahl [audio]", "Kon-tiki", meta)
    assert audit_folder(folder, meta, con).proposed == {"authors": ["Heyerdahl Thor"],
                                                        "narrators": ["Karel Höeger"]}
    meta = {"title": "Urednici", "narrators": ["Guy de Maupassant"],
            "authors": ["Psano kurzivou (12.08.2016) - Guy de Maupassant - CRo Vltava"]}
    folder = _folder(tmp_path, "Guy de Maupassant [audio]", "Urednici", meta)
    assert audit_folder(folder, meta, con).proposed == {"authors": ["Guy de Maupassant"],
                                                        "narrators": []}


def test_wrong_author_keeps_a_known_reader_as_narrator(tmp_path):
    con = _index(tmp_path, [_post(14, "Strakatý pes na břehu moře (1978)", [
        "Čingiz Ajtmatov. Novela.", "Čte Václav Voska.", "Natočeno 1978."])])
    meta = {"title": "Strakatý pes na břehu moře", "authors": ["(Václav Voska)1978(1h58m11s)"],
            "narrators": None}
    folder = _folder(tmp_path, "Cingiz Ajtmatov [audio]", "Strakaty pes", meta)
    f = audit_folder(folder, meta, con)
    assert f.verdict in ("swapped", "wrong_author") and f.confident
    assert f.proposed == {"authors": ["Čingiz Ajtmatov"], "narrators": ["Václav Voska"]}


def test_reversed_name_order_is_the_same_person(tmp_path):
    con = _index(tmp_path, [_post(15, "Bratři Lví srdce (1990)", [
        "Astrid Lindgrenová. Pohádka.", "Čte Martin Růžek.", "Natočeno 1990."])])
    meta = {"title": "Bratři Lví srdce", "authors": ["Lindgren Astrid"], "narrators": None}
    folder = _folder(tmp_path, "Astrid Lindgren [audio]", "Bratri", meta)
    assert audit_folder(folder, meta, con).verdict == "ok"


def test_undo_restores_the_logged_values(tmp_path):
    from audiobiblio.library.shelf_audit import undo_changes
    con = _index(tmp_path, [GORIOT])
    folder = _folder(tmp_path, "Honore de Balzac [audio]", "Otec Goriot", SWAPPED)
    log = tmp_path / "changes.jsonl"
    apply_finding(audit_folder(folder, SWAPPED, con), log)
    assert undo_changes(log, since=None) == 1
    assert json.loads((folder / "metadata.json").read_text()) == SWAPPED
