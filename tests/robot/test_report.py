"""Phase-1 report on a tiny fake share (no real audio: durations patched)."""
from __future__ import annotations

from audiobiblio.robot import inventory, report


def _touch(p, size=10):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def test_report_verdicts(tmp_path, monkeypatch):
    eb = tmp_path / "ebooks"
    _touch(eb / "eBOOKs.fiction/Jan Autor [audio]/Jan Autor - (2020) Kniha/01.mp3")
    _touch(eb / "eBOOKs.temp/Jan Autor - Kniha/01.mp3")                 # copy of shelved
    _touch(eb / "eBOOKs.temp/Autor Jan - Kniha (cte X)2020(2h)/01.mp3")  # other version (length)
    _touch(eb / "eBOOKs.temp2sort/Eva Nova - Nova kniha/01.mp3")        # new
    lengths = {"Kniha/01.mp3": 3600, "Jan Autor - Kniha/01.mp3": 3610,
               "(2h)/01.mp3": 7200, "Nova kniha/01.mp3": 1800}

    def fake_measure(files):
        f = files[0]
        dur = next(v for k, v in lengths.items() if f.endswith(k))
        return {"bytes": 10, "duration_s": dur, "album": None, "artist": None, "date": None, "genre": None}

    monkeypatch.setattr(inventory, "_measure", fake_measure)
    monkeypatch.setattr(report, "EBOOKS", eb)
    con = inventory.connect(tmp_path / "r.sqlite3")
    for rel in ("eBOOKs.fiction", "eBOOKs.temp", "eBOOKs.temp2sort"):
        inventory.scan_root(con, eb / rel)
    rep = report.build_report(con, tmp_path)
    assert rep["counts"] == {"copy_of_shelved": 1, "other_version": 1, "new": 1}
    assert (tmp_path / "robot_report.tsv").exists()


def test_incremental_scan_skips_unchanged(tmp_path, monkeypatch):
    eb = tmp_path / "ebooks"
    _touch(eb / "eBOOKs.temp/A - B/01.mp3")
    calls = []
    monkeypatch.setattr(inventory, "_measure",
                        lambda files: calls.append(files) or {"bytes": 1, "duration_s": 1, "album": None,
                                                             "artist": None, "date": None, "genre": None})
    con = inventory.connect(tmp_path / "r.sqlite3")
    inventory.scan_root(con, eb / "eBOOKs.temp")
    inventory.scan_root(con, eb / "eBOOKs.temp")
    assert len(calls) == 1
