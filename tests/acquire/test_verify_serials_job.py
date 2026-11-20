"""The daily verifier job is report-only and never takes the scheduler down."""
from __future__ import annotations

import json

from audiobiblio.acquire import scheduler, serial_verify


def test_job_writes_the_report_and_does_not_fix(monkeypatch, tmp_path):
    rep = serial_verify.VerifyReport(checked=3, skipped=1)
    rep.incomplete.append(serial_verify.BookCheck(7, "Kniha", "u", have=(1, 3), rapi_total=3,
                                                  missing_live=(2,)))
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: object())
    monkeypatch.setattr(serial_verify, "verify_all", lambda s, limit: rep)
    monkeypatch.setattr("audiobiblio.paths.get_dirs", lambda: {"data": tmp_path})
    scheduler._verify_serials_job()
    out = json.loads((tmp_path / "serial_verify.json").read_text())
    assert out["checked"] == 3 and out["missing_live"] == 1
    assert out["incomplete"][0]["missing_live"] == [2]


def test_job_swallows_errors(monkeypatch):
    monkeypatch.setattr("audiobiblio.core.db.session.get_session", lambda: object())

    def boom(s, limit):
        raise RuntimeError("rAPI down")
    monkeypatch.setattr(serial_verify, "verify_all", boom)
    scheduler._verify_serials_job()          # logged as verify_serials_error, no exception
