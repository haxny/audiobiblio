"""Searchable index of parsed mluvenypanacek.cz records (own SQLite + FTS5).

  build_index(dump_dir, db_path)  — posts.jsonl + changes-*.jsonl (latest wins)
  find(con, title, author=, narrator=, minutes=) — ranked candidates

Ranking: title match is required; author / narrator agreement and a length
within ±10 % (per part or in total) raise confidence. The caller decides
what is "the same version" — this only orders the evidence.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from unidecode import unidecode

from audiobiblio.sources.panacek_parse import parse_record

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY, link TEXT, category TEXT, title_base TEXT, alt_title TEXT,
  part INTEGER, parts_total INTEGER, title_years TEXT, authors TEXT, narrators TEXT,
  cast_json TEXT, credits TEXT, recorded_years TEXT, part_minutes TEXT,
  total_minutes INTEGER, release_minutes INTEGER, parts TEXT, modified TEXT, text TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
  title, people, content='', tokenize='unicode61 remove_diacritics 2'
);
"""


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", unidecode(s or "").lower())).strip()


def build_index(dump_dir: Path, db_path: Path) -> dict:
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    files = [dump_dir / "posts.jsonl"] + sorted(dump_dir.glob("changes-*.jsonl"))
    latest: dict[int, dict] = {}
    for f in files:
        if f.exists():
            for line in f.open():
                rec = json.loads(line)
                latest[rec["id"]] = rec            # later files win
    for rec in latest.values():
        r = parse_record(rec)
        people = r["authors"] + r["narrators"] + [c["actor"] for c in r["cast"]]
        con.execute("insert into records values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["id"], r["link"], r["category"], r["title_base"], r["alt_title"], r["part"],
            r["parts_total"], json.dumps(r["title_years"]), json.dumps(r["authors"], ensure_ascii=False),
            json.dumps(r["narrators"], ensure_ascii=False), json.dumps(r["cast"], ensure_ascii=False),
            json.dumps(r["credits"], ensure_ascii=False), json.dumps(r["recorded_years"]),
            json.dumps(r["part_minutes"]), r["total_minutes"], r["release_minutes"],
            json.dumps(r["parts"], ensure_ascii=False), r["modified"], r["text"]))
        con.execute("insert into records_fts(rowid, title, people) values (?,?,?)",
                    (r["id"], f"{r['title_base']} {r['alt_title'] or ''}", " ".join(people)))
    con.commit()
    return {"records": len(latest)}


def _fts_query(s: str) -> str:
    toks = [t for t in _norm(s).split() if len(t) > 2][:6]
    return " AND ".join(f'"{t}"' for t in toks)


def find(con: sqlite3.Connection, title: str, author: str | None = None,
         narrator: str | None = None, minutes: float | None = None, limit: int = 10) -> list[dict]:
    q = _fts_query(title)
    if not q:
        return []
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select r.* from records_fts f join records r on r.id = f.rowid "
        "where records_fts match ? limit 200", (f"title: ({q})",)).fetchall()
    want_t, out = _norm(title), []
    for r in rows:
        score = 0.0
        t = _norm(r["title_base"])
        score += 3 if t == want_t else (1.5 if want_t in t or t in want_t else 0.5)
        people = _norm(" ".join(json.loads(r["authors"]) + json.loads(r["narrators"])
                                + [c["actor"] for c in json.loads(r["cast_json"])]))
        if author and all(tok in people for tok in _norm(author).split()):
            score += 2
        if narrator and all(tok in people for tok in _norm(narrator).split()):
            score += 2
        if minutes:
            cands = [m for m in (r["total_minutes"], r["release_minutes"]) if m]
            cands += json.loads(r["part_minutes"])
            if any(abs(m - minutes) / max(m, minutes) <= 0.10 for m in cands):
                score += 2
        out.append({**dict(r), "score": score})
    return sorted(out, key=lambda d: -d["score"])[:limit]
