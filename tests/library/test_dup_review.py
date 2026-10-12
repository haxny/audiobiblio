"""Temp copies of shelved books: the system proposes, the user decides, the
executor acts — always via #recycle, re-verifying before every move."""
from __future__ import annotations

import pytest

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, DupCandidate
from audiobiblio.library import dup_review as dr


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    base = tmp_path / "ebooks"
    monkeypatch.setattr(dr, "BASE", base)
    monkeypatch.setattr(dr, "RECYCLE", base / "#recycle" / "audiobiblio-dups")
    return base


def _book(d, sizes):
    d.mkdir(parents=True)
    for i, n in enumerate(sizes, 1):
        (d / f"{i:02d}.mp3").write_bytes(b"x" * n)
    return d


def _cand(db, roots, temp_sizes, shelf_sizes, name="Kniha"):
    t = _book(roots / "eBOOKs.temp" / name, temp_sizes)
    sh = _book(roots / "eBOOKs.fiction" / "Autor [audio]" / name, shelf_sizes)
    dr.import_rows(db, [{"temp": str(t), "shelf": str(sh), "MB_temp": sum(temp_sizes) / 1e6,
                         "files_temp": len(temp_sizes), "MB_shelf": sum(shelf_sizes) / 1e6,
                         "files_shelf": len(shelf_sizes), "min_temp": "60"}])
    return db.query(DupCandidate).filter_by(temp_path=str(t)).one(), t, sh


def test_identical_copy_is_proposed_for_recycle(db_session, roots):
    c, _, _ = _cand(db_session, roots, [10, 20], [10, 20])
    assert c.verdict == "IDENTICAL" and c.proposal == dr.RECYCLE_TEMP and c.decision is None


def test_differing_copy_has_no_proposal(db_session, roots):
    c, _, _ = _cand(db_session, roots, [10, 20], [10, 99])
    assert c.verdict == "shelf_bigger" and c.proposal is None


def test_accept_proposals_then_execute_recycles_temp(db_session, roots):
    c, t, sh = _cand(db_session, roots, [10, 20], [10, 20])
    assert dr.accept_proposals(db_session) == 1
    done = dr.execute(db_session)
    assert done == {"done": 1}
    assert not t.exists() and sh.is_dir()
    assert (roots / "#recycle" / "audiobiblio-dups" / "eBOOKs.temp" / "Kniha" / "01.mp3").exists()
    assert c.status == "done"


def test_identical_recheck_refuses_when_temp_changed(db_session, roots):
    c, t, _ = _cand(db_session, roots, [10, 20], [10, 20])
    dr.accept_proposals(db_session)
    (t / "03.mp3").write_bytes(b"new")          # the copy grew since the scan
    assert dr.execute(db_session) == {"error": 1}
    assert t.is_dir() and "nejsou" in c.result


def test_replace_shelf_swaps_folders(db_session, roots):
    c, t, sh = _cand(db_session, roots, [10, 99], [10, 20])
    dr.decide(db_session, [c.id], dr.REPLACE_SHELF)
    assert dr.execute(db_session) == {"done": 1}
    assert not t.exists()
    assert sorted(p.stat().st_size for p in sh.iterdir()) == [10, 99]
    old = roots / "#recycle" / "audiobiblio-dups" / "eBOOKs.fiction" / "Autor [audio]" / "Kniha"
    assert sorted(p.stat().st_size for p in old.iterdir()) == [10, 20]


def test_replace_refused_for_shelf_managed_by_audiobiblio(db_session, roots, episode_factory):
    c, t, sh = _cand(db_session, roots, [10, 99], [10, 20])
    ep = episode_factory()
    db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                         file_path=str(sh / "01.mp3")))
    db_session.flush()
    dr.decide(db_session, [c.id], dr.REPLACE_SHELF)
    assert dr.execute(db_session) == {"error": 1}
    assert t.is_dir() and (sh / "02.mp3").stat().st_size == 20


def test_keep_both_is_remembered_and_never_reopened(db_session, roots):
    c, t, _ = _cand(db_session, roots, [10, 99], [10, 20])
    dr.decide(db_session, [c.id], dr.KEEP_BOTH)
    dr.execute(db_session)
    assert c.status == "done" and t.is_dir()
    dr.import_rows(db_session, [{"temp": str(t), "shelf": c.shelf_path, "MB_temp": 1, "files_temp": 2,
                                 "MB_shelf": 1, "files_shelf": 2, "min_temp": ""}])
    assert c.decision == dr.KEEP_BOTH and c.status == "done"


def test_vanished_folder_closes_the_candidate(db_session, roots):
    import shutil
    c, t, _ = _cand(db_session, roots, [10], [10])
    dr.accept_proposals(db_session)
    shutil.rmtree(t)
    assert dr.execute(db_session) == {"gone": 1}

