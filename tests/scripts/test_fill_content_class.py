"""scripts/fill_content_class.py — crawl_targets.content_class from the
2026-11-07 classification JSON; dry-run by default, idempotent."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from audiobiblio.core.db.models import ApprovalMode, CrawlTarget, CrawlTargetKind

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fill_content_class.py"


def _load():
    spec = importlib.util.spec_from_file_location("fill_content_class", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


def _t(db, tid, name, mode=ApprovalMode.REVIEW, cls=None):
    t = CrawlTarget(id=tid, url=f"https://x.rozhlas.cz/{tid}", kind=CrawlTargetKind.PROGRAM,
                    name=name, approval_mode=mode, content_class=cls)
    db.add(t)
    db.flush()
    return t


def _json(tmp_path, mapping):
    targets = {}
    for tid, cls in mapping.items():
        targets.setdefault(cls, []).append({"id": tid, "name": f"t{tid}", "cls": cls})
    p = tmp_path / "classified.json"
    p.write_text(json.dumps({"summary": {}, "targets": targets}))
    return p


def test_load_mapping(mod, tmp_path):
    p = _json(tmp_path, {1: "SERIAL", 2: "NEWS/TALK"})
    assert mod.load_mapping(p) == {1: "SERIAL", 2: "NEWS/TALK"}


def test_load_mapping_rejects_unknown_class(mod, tmp_path):
    p = _json(tmp_path, {1: "POETRY"})
    with pytest.raises(ValueError):
        mod.load_mapping(p)


def test_dry_run_changes_nothing(mod, db_session, tmp_path):
    _t(db_session, 1, "Auditorium")
    report = mod.fill(db_session, mod.load_mapping(_json(tmp_path, {1: "SERIAL"})),
                      apply=False)
    assert report.planned == {1: "SERIAL"}
    assert db_session.get(CrawlTarget, 1).content_class is None


def test_apply_fills_and_is_idempotent(mod, db_session, tmp_path):
    _t(db_session, 1, "Auditorium")
    _t(db_session, 2, "Zprávy")
    mapping = mod.load_mapping(_json(tmp_path, {1: "SERIAL", 2: "NEWS/TALK", 99: "BOOK"}))
    report = mod.fill(db_session, mapping, apply=True)
    assert db_session.get(CrawlTarget, 1).content_class == "SERIAL"
    assert report.missing_ids == [99]
    assert report.per_class == {"SERIAL": 1, "NEWS/TALK": 1}
    again = mod.fill(db_session, mapping, apply=True)
    assert again.planned == {} and again.unchanged == 2


def test_existing_class_kept_unless_overwrite(mod, db_session, tmp_path):
    _t(db_session, 1, "Auditorium", cls="DRAMA")
    mapping = mod.load_mapping(_json(tmp_path, {1: "SERIAL"}))
    report = mod.fill(db_session, mapping, apply=True)
    assert report.conflicts == {1: ("DRAMA", "SERIAL")}
    assert db_session.get(CrawlTarget, 1).content_class == "DRAMA"
    mod.fill(db_session, mapping, apply=True, overwrite=True)
    assert db_session.get(CrawlTarget, 1).content_class == "SERIAL"


@pytest.mark.parametrize("name,expected", [
    ("Cetba na pokracovani (CRo2) — stanice", "BOOK"),
    ("Povídka na Dvojce", "BOOK"),
    ("Radiokniha (CRo+)", "BOOK"),
    ("Hra na nedeli (CRo2)", "DRAMA"),
    ("Večerní drama", "DRAMA"),
    ("Rozhlasová hra", "DRAMA"),
    ("Hrana", None),               # 'hra' only as a whole word
    ("Folková pohlazení", "MUSIC-OTHER"),
    ("Toulky českou minulostí", None),
])
def test_guess_class(mod, name, expected):
    assert mod.guess_class(name) == expected


def test_owner_overrides_classify_toulky_and_osudy_as_books(mod, db_session, tmp_path):
    """Owner decision 2026-11-07: both are rank 1 (BOOK) though unlisted."""
    _t(db_session, 69, "Toulky českou minulostí", mode=ApprovalMode.AUTO)
    _t(db_session, 51, "osudy", mode=ApprovalMode.AUTO)
    mapping = mod.load_mapping(_json(tmp_path, {}))
    report = mod.fill(db_session, mapping, apply=True)
    assert report.planned == {51: "BOOK", 69: "BOOK"}
    assert db_session.get(CrawlTarget, 69).content_class == "BOOK"


def test_guess_only_for_unlisted_auto_targets_when_asked(mod, db_session, tmp_path):
    _t(db_session, 5, "Hra na sobotu", mode=ApprovalMode.AUTO)
    _t(db_session, 6, "Hra pro pamětníky", mode=ApprovalMode.REVIEW)
    _t(db_session, 7, "Toulky českou minulostí", mode=ApprovalMode.AUTO)
    mapping = mod.load_mapping(_json(tmp_path, {}))
    assert mod.fill(db_session, mapping, apply=False).planned == {}
    report = mod.fill(db_session, mapping, apply=True, guess_unlisted=True)
    assert report.planned == {5: "DRAMA"}
    assert report.unclassified_auto == [(7, "Toulky českou minulostí")]
