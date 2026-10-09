"""mluvenypanacek.cz sync: full dump once, then monthly changes only."""
from __future__ import annotations

import json

from audiobiblio.sources import mluvenypanacek as mp


def _fake_fetch(pages):
    calls = []

    def fetch(page, modified_after):
        calls.append((page, modified_after))
        return (pages[page - 1], len(pages)) if page <= len(pages) else ([], 0)
    return fetch, calls


def test_full_dump_then_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "GAP_S", 0)
    fetch, calls = _fake_fetch([[{"id": 1}], [{"id": 2}]])
    monkeypatch.setattr(mp, "fetch", fetch)
    st = mp.sync(tmp_path, now="2026-10-01T02:15:00")
    assert st == {"file": "posts.jsonl", "posts": 2, "modified_after": None}
    assert [json.loads(l)["id"] for l in (tmp_path / "posts.jsonl").read_text().splitlines()] == [1, 2]

    fetch2, calls2 = _fake_fetch([[{"id": 2, "modified": "x"}]])
    monkeypatch.setattr(mp, "fetch", fetch2)
    st2 = mp.sync(tmp_path, now="2026-11-01T02:15:00")
    assert st2["modified_after"] == "2026-10-01T02:15:00"      # since the last sync
    assert calls2[0] == (1, "2026-10-01T02:15:00")
    assert (tmp_path / "changes-2026-11-01.jsonl").exists()


def test_interrupted_dump_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "GAP_S", 0)
    (tmp_path / "posts.jsonl").write_text('{"id": 1}\n')
    (tmp_path / "posts.jsonl.page").write_text("1")
    fetch, calls = _fake_fetch([[{"id": 1}], [{"id": 2}]])
    monkeypatch.setattr(mp, "fetch", fetch)
    mp.sync(tmp_path, now="2026-10-01T00:00:00")
    assert calls[0][0] == 2                                      # resumed after page 1
