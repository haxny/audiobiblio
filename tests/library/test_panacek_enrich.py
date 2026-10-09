"""Matching works to mluvenypanacek records and planning field changes."""
from __future__ import annotations

import json
import sqlite3

from audiobiblio.library.panacek_enrich import match_work, plan_work, split_title
from audiobiblio.sources.panacek_index import build_index


def _post(pid, title, lines, cats=(3,)):
    return {"id": pid, "link": f"https://mluvenypanacek.cz/x/{pid}.html", "categories": list(cats),
            "title": {"rendered": title}, "content": {"rendered": "".join(f"<p>{l}</p>" for l in lines)},
            "date": "2020", "modified": "2020"}


def _index(tmp_path, posts):
    d = tmp_path / "dump"; d.mkdir()
    (d / "posts.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in posts))
    build_index(d, tmp_path / "p.sqlite3")
    return sqlite3.connect(tmp_path / "p.sqlite3")


def test_split_title_takes_author_prefix_and_drops_subtitle():
    assert split_title("Alois Jirásek: Staré pověsti české. Podtitul", None) == ("Staré pověsti české", "Alois Jirásek")
    assert split_title("Rybářské historky. Desatero humorných vyprávění", None) == ("Rybářské historky", None)


def test_unique_title_matches_without_author(tmp_path):
    con = _index(tmp_path, [_post(1, "Rybářské historky 1/10 (2014)",
                                  ["Vlastimil Brouček. Humorné vyprávění.", "Čte Igor Bareš.", "Natočeno 2014."])])
    m = match_work(con, "Rybářské historky", None, None, None)
    assert m.verdict == "matched" and m.record["id"] == 1


def test_same_title_different_works_needs_author(tmp_path):
    con = _index(tmp_path, [
        _post(1, "Podvodníci 1/10 (2017)", ["Barbora Haplová. Fantasy seriál.", "Natočeno 2017."], cats=(4,)),
        _post(2, "Podvodníci (1991)", ["Bohumil Hrabal. Povídka.", "Čte Vladimír Brabec.", "Natočeno 1991."], cats=(2,)),
    ])
    assert match_work(con, "Podvodníci", "Barbora Haplova", None, None).record["id"] == 1
    assert match_work(con, "Podvodníci", None, None, None).verdict == "ambiguous"


def test_plan_fills_empty_and_flags_conflicts(tmp_path, db_session, episode_factory):
    from audiobiblio.core.db.models import FieldOrigin, MetadataValue
    con = _index(tmp_path, [_post(1, "Staré pověsti české 1/10 (2008)",
                                  ["Alois Jirásek. Výběr.", "Čte Jiří Schwarz.", "Natočeno 2008."])])
    ep = episode_factory()
    w = ep.work
    w.title, w.author = "Stare povesti ceske", "Alois Jirasek"
    db_session.add(MetadataValue(entity_type="work", entity_id=w.id, field="publisher",
                                 value="CRo3 2009", origin=FieldOrigin.MANUAL, source="user"))
    db_session.flush()
    plan = plan_work(db_session, con, w)
    assert plan["verdict"] == "matched"
    f = plan["fields"]
    assert f["author"]["action"] == "same"
    assert f["narrator"] == {"current": None, "proposed": "Jiri Schwarz", "action": "fill"}
    assert f["recorded_year"]["action"] == "conflict" and f["recorded_year"]["proposed"] == "2008"
    assert f["parts_total"] == {"current": None, "proposed": "10", "action": "fill"}


def test_scraped_air_year_is_replaced_not_conflict(tmp_path, db_session, episode_factory):
    from audiobiblio.core.db.models import FieldOrigin, MetadataValue
    con = _index(tmp_path, [_post(1, "Kniha X 1/3 (2005)", ["Jan Autor. Román.", "Čte Petr Cteci.", "Natočeno 2005."])])
    ep = episode_factory()
    w = ep.work
    w.title, w.author = "Kniha X", "Jan Autor"
    db_session.add(MetadataValue(entity_type="work", entity_id=w.id, field="publisher",
                                 value="CRo3 2022", origin=FieldOrigin.FILE, source="f"))
    db_session.flush()
    f = plan_work(db_session, con, w)["fields"]
    assert f["recorded_year"]["action"] == "replace"


def test_real_author_not_in_record_rejects_match(tmp_path, db_session, episode_factory):
    con = _index(tmp_path, [_post(1, "Cesta po řece (2019)", ["Andrej Vejcler. Povídka.", "Čte Jan Herec.", "Natočeno 2019."])])
    ep = episode_factory(); w = ep.work
    w.title, w.author = "Cesta po řece", "Jiri Orten"
    db_session.flush()
    assert plan_work(db_session, con, w)["verdict"] == "rejected_author"


def test_placeholder_author_is_replaced(tmp_path, db_session, episode_factory):
    con = _index(tmp_path, [_post(1, "O poctivém groši (1980)", ["Alena Riegerová. Pohádka.", "Čte Jan Herec.", "Natočeno 1980."])])
    ep = episode_factory(); w = ep.work
    w.title, w.author = "O poctivém groši", "Redakce Radia Junior"
    db_session.flush()
    p = plan_work(db_session, con, w)
    assert p["fields"]["author"]["action"] == "replace" and p["fields"]["author"]["proposed"] == "Alena Riegerova"


def test_apply_writes_fills_and_skips_conflicts(tmp_path, db_session, episode_factory):
    from audiobiblio.core.db.models import FieldOrigin, MetadataValue
    from audiobiblio.library.panacek_enrich import apply_plan
    ep = episode_factory(); w = ep.work
    plan = {"link": "https://mluvenypanacek.cz/x/1.html", "fields": {
        "author": {"current": "Redakce Radia Junior", "proposed": "Alena Riegerova", "action": "replace"},
        "narrator": {"current": None, "proposed": "Jan Herec", "action": "fill"},
        "recorded_year": {"current": "2009", "proposed": "2008", "action": "conflict"},
        "parts_total": {"current": None, "proposed": "10", "action": "fill"}}}
    assert apply_plan(db_session, w, plan) == ["author", "narrator", "parts_total"]
    db_session.flush()
    assert w.author == "Alena Riegerova" and w.expected_total == 10 and w.expected_source == "panacek"
    nar = db_session.query(MetadataValue).filter_by(entity_type="episode", entity_id=ep.id, field="narrator").one()
    assert (nar.value, nar.origin, nar.source) == ("Jan Herec", FieldOrigin.ENRICHED, plan["link"])
    assert db_session.query(MetadataValue).filter_by(entity_type="work", entity_id=w.id, field="publisher").count() == 0
