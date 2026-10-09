"""Index + lookup over parsed mluvenypanacek records."""
from __future__ import annotations

import json
import sqlite3

from audiobiblio.sources.panacek_index import build_index, find


def _post(pid, title, lines, cats=(3,)):
    return {"id": pid, "link": f"https://mluvenypanacek.cz/x/{pid}.html", "categories": list(cats),
            "title": {"rendered": title}, "content": {"rendered": "".join(f"<p>{l}</p>" for l in lines)},
            "date": "2020", "modified": "2020"}


def test_find_prefers_matching_narrator_and_length(tmp_path):
    d = tmp_path / "dump"; d.mkdir()
    posts = [
        _post(1, "Staré pověsti české 1/38 (1990-1991, 2011)",
              ["Miloslav Steiner. Režie Mária Křepelková.",
               "Osoby a obsazení: vypravěč (Eduard Cupák), hrabě Kroll (Jiří Schwarz).",
               "Natočeno 1991 (38 x 18 min.)."], cats=(13, 4)),
        _post(2, "Staré pověsti české (2009)",
              ["Alois Jirásek. Výběr. Režie Ivan Chrz.", "Čte Jiří Schwarz.", "Natočeno 2009 (15 x 26 min.)."]),
    ]
    (d / "posts.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in posts))
    db = tmp_path / "p.sqlite3"
    assert build_index(d, db) == {"records": 2}
    con = sqlite3.connect(db)
    hits = find(con, "Stare povesti ceske", author="Alois Jirasek", narrator="Jiri Schwarz", minutes=26)
    assert hits[0]["id"] == 2
    assert json.loads(hits[0]["narrators"]) == ["Jiří Schwarz"]


def test_changes_file_overrides_dump(tmp_path):
    d = tmp_path / "dump"; d.mkdir()
    (d / "posts.jsonl").write_text(json.dumps(_post(1, "Kniha (2000)", ["Jan Autor. X.", "Čte Petr Stary."])))
    (d / "changes-2026-11-01.jsonl").write_text(json.dumps(_post(1, "Kniha (2000)", ["Jan Autor. X.", "Čte Petr Novy."])))
    db = tmp_path / "p.sqlite3"
    build_index(d, db)
    con = sqlite3.connect(db)
    assert json.loads(find(con, "Kniha")[0]["narrators"]) == ["Petr Novy"]
