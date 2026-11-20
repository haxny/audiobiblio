"""The daily verifier job is report-only and never takes the scheduler down."""
from __future__ import annotations

import json

from audiobiblio.acquire import scheduler, serial_verify


def test_job_writes_the_report_and_repairs_within_budget(monkeypatch, tmp_path):
    rep = serial_verify.VerifyReport(checked=3, skipped=1)
    rep.incomplete.append(serial_verify.BookCheck(7, "Kniha", "u", have=(1, 3), rapi_total=3,
                                                  missing_live=(2,)))
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: object())
    monkeypatch.setattr(serial_verify, "verify_all", lambda s, limit: rep)
    monkeypatch.setattr("audiobiblio.paths.get_dirs", lambda: {"data": tmp_path})
    calls = []
    monkeypatch.setattr(serial_verify, "repair_missing",
                        lambda s, r, max_parts: calls.append(max_parts) or serial_verify.RepairResult(2, 1, 0))
    scheduler._verify_serials_job()
    out = json.loads((tmp_path / "serial_verify.json").read_text())
    assert out["checked"] == 3 and out["missing_live"] == 1
    assert out["incomplete"][0]["missing_live"] == [2]
    assert calls == [scheduler.VERIFY_REPAIR_PARTS_PER_NIGHT]
    assert out["repaired"] == {"added": 2, "books": 1, "skipped_split": 0}


def test_repair_can_be_switched_off(monkeypatch, tmp_path):
    rep = serial_verify.VerifyReport(checked=1)
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: object())
    monkeypatch.setattr(serial_verify, "verify_all", lambda s, limit: rep)
    monkeypatch.setattr("audiobiblio.paths.get_dirs", lambda: {"data": tmp_path})
    monkeypatch.setattr(serial_verify, "repair_missing",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setenv("AUDIOBIBLIO_VERIFY_REPAIR", "0")
    scheduler._verify_serials_job()
    assert "repaired" not in json.loads((tmp_path / "serial_verify.json").read_text())


def test_job_swallows_errors(monkeypatch):
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: object())

    def boom(s, limit):
        raise RuntimeError("rAPI down")
    monkeypatch.setattr(serial_verify, "verify_all", boom)
    scheduler._verify_serials_job()          # logged as verify_serials_error, no exception
