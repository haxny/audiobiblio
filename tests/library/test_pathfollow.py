"""Audiobiblio follows the files: rename/move/retag on disk (cmdr, foobar) must
not orphan the DB (asset.file_path, work final_path) — the disk is the truth."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, FieldOrigin, Program, Series, Station, Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.library import pathfollow
from audiobiblio.tags.writer import write_tags


def _alter_audio(path: Path) -> None:
    """Make the audio itself different (flip a byte inside the payload)."""
    start, end = pathfollow._payload_range(path)
    data = bytearray(path.read_bytes())
    data[end - 1] ^= 0xFF
    path.write_bytes(bytes(data))


def _book(db, files: list[Path], final_path: Path | None = None, title="Kniha"):
    """One work with one episode+COMPLETE audio asset per file."""
    st = db.query(Station).first() or Station(code="x", name="X")
    db.add(st)
    db.flush()
    p = Program(station_id=st.id, name="P")
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name="P")
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=title)
    db.add(w)
    db.flush()
    assets = []
    for n, f in enumerate(files, 1):
        ep = Episode(work_id=w.id, title=f"d{n}", episode_number=n)
        db.add(ep)
        db.flush()
        a = Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                  file_path=str(f))
        db.add(a)
        db.flush()
        assert pathfollow.fingerprint_asset(a)
        assets.append(a)
    if final_path is not None:
        record_value(db, "work", w.id, "final_path", str(final_path), FieldOrigin.MANUAL, "t")
    db.flush()
    return w, assets


def test_fingerprint_survives_tag_edit_and_rename(silent_m4a_factory, tmp_path):
    f = silent_m4a_factory(tmp_path / "a.m4a")
    before = pathfollow.audio_fingerprint(f)
    write_tags(str(f), {"performer": "Foobar Edit", "genre": "x"}, {"title": "Nove"})
    renamed = f.rename(tmp_path / "zcela jine jmeno.m4a")
    assert pathfollow.audio_fingerprint(renamed) == before
    assert pathfollow.payload_len(renamed) == int(before.split(":")[0])


def test_different_audio_has_different_fingerprint(silent_m4a_factory, tmp_path):
    a = silent_m4a_factory(tmp_path / "a.m4a")
    b = tmp_path / "b.m4a"
    shutil.copy(a, b)
    _alter_audio(b)
    assert pathfollow.audio_fingerprint(a) != pathfollow.audio_fingerprint(b)


def test_folder_rename_is_followed(db_session, silent_m4a_factory, tmp_path):
    old = tmp_path / "shelf" / "Stara slozka"
    old.mkdir(parents=True)
    f1, f2 = silent_m4a_factory(old / "01.m4a"), silent_m4a_factory(old / "02.m4a")
    _alter_audio(f2)                                        # distinct from part 1
    w, assets = _book(db_session, [f1, f2], final_path=old)
    for a in assets:
        pathfollow.fingerprint_asset(a)
    new = tmp_path / "shelf" / "Nova slozka (cte Nekdo, CRo 1989)"
    old.rename(new)
    (new / "01.m4a").rename(new / "Dil 1.m4a")             # and a file renamed too

    rep = pathfollow.follow_paths(db_session, [tmp_path / "shelf"], apply=True)
    assert len(rep.moved) == 2 and not rep.ambiguous and not rep.unmatched
    assert Path(assets[0].file_path) == new / "Dil 1.m4a"
    assert Path(assets[1].file_path) == new / "02.m4a"
    assert assets[0].extra["path_history"][-1]["from"] == str(f1)
    assert rep.works_updated == [(w.id, str(old), str(new))]


def test_dry_run_changes_nothing(db_session, silent_m4a_factory, tmp_path):
    d = tmp_path / "s" / "a"
    d.mkdir(parents=True)
    f = silent_m4a_factory(d / "01.m4a")
    _w, (a,) = _book(db_session, [f])
    moved = tmp_path / "s" / "b"
    d.rename(moved)
    rep = pathfollow.follow_paths(db_session, [tmp_path / "s"], apply=False)
    assert len(rep.moved) == 1
    assert a.file_path == str(f)


def test_moved_to_another_root_and_missing_asset_revived(db_session, silent_m4a_factory, tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "fiction").mkdir()
    f = silent_m4a_factory(tmp_path / "work" / "01.m4a")
    _w, (a,) = _book(db_session, [f])
    shutil.move(str(f), str(tmp_path / "fiction" / "01.m4a"))
    a.status = AssetStatus.MISSING                        # verify-files had marked it
    rep = pathfollow.follow_paths(db_session, [tmp_path / "work", tmp_path / "fiction"], apply=True)
    assert len(rep.moved) == 1
    assert a.status == AssetStatus.COMPLETE and a.file_path == str(tmp_path / "fiction" / "01.m4a")


def test_two_identical_copies_are_ambiguous_not_guessed(db_session, silent_m4a_factory, tmp_path):
    (tmp_path / "s").mkdir()
    f = silent_m4a_factory(tmp_path / "s" / "01.m4a")
    _w, (a,) = _book(db_session, [f])
    shutil.copy(f, tmp_path / "s" / "kopie1.m4a")
    shutil.copy(f, tmp_path / "s" / "kopie2.m4a")
    f.unlink()
    rep = pathfollow.follow_paths(db_session, [tmp_path / "s"], apply=True)
    assert rep.moved == [] and len(rep.ambiguous) == 1
    assert a.file_path == str(f)


def test_vanished_file_is_reported_unmatched(db_session, silent_m4a_factory, tmp_path):
    (tmp_path / "s").mkdir()
    f = silent_m4a_factory(tmp_path / "s" / "01.m4a")
    _w, (a,) = _book(db_session, [f])
    f.unlink()
    rep = pathfollow.follow_paths(db_session, [tmp_path / "s"], apply=True)
    assert rep.unmatched == [(a.id, str(f))]


def test_sidecar_assets_follow_their_audio(db_session, silent_m4a_factory, tmp_path):
    d = tmp_path / "s" / "a"
    d.mkdir(parents=True)
    f = silent_m4a_factory(d / "01.m4a")
    (d / "01.info.json").write_text("{}")
    _w, (a,) = _book(db_session, [f])
    side = Asset(episode_id=a.episode_id, type=AssetType.META_JSON,
                 status=AssetStatus.COMPLETE, file_path=str(d / "01.info.json"))
    db_session.add(side)
    db_session.flush()
    d.rename(tmp_path / "s" / "b")
    pathfollow.follow_paths(db_session, [tmp_path / "s"], apply=True)
    assert side.file_path == str(tmp_path / "s" / "b" / "01.info.json")


# --- folder name -> metadata (the user renamed the folder by hand) ---------------

def test_parse_book_dir_full_pattern():
    p = pathfollow.parse_book_dir("Bohumil Hrabal - (1989) Tanecni hodiny pro starsi a pokrocile (cte Lubor Tokos, CRo 1989)")
    assert p == {"author": "Bohumil Hrabal", "year": "1989",
                 "title": "Tanecni hodiny pro starsi a pokrocile",
                 "narrator": "Lubor Tokos", "publisher": "CRo 1989"}


def test_parse_book_dir_without_year_or_suffix():
    assert pathfollow.parse_book_dir("Petra Klabouchova - Prosecke peklo") == {
        "author": "Petra Klabouchova", "title": "Prosecke peklo"}
    assert pathfollow.parse_book_dir("Petra Klabouchova - Prosecke peklo (cte Libor Hruska, CRo)") == {
        "author": "Petra Klabouchova", "title": "Prosecke peklo", "narrator": "Libor Hruska"}


def test_parse_book_dir_rejects_non_book_names():
    assert pathfollow.parse_book_dir("nahodna slozka") is None
    assert pathfollow.parse_book_dir("Autor - ") is None


def test_renamed_folder_metadata_becomes_manual(db_session, silent_m4a_factory, tmp_path):
    old = tmp_path / "shelf" / "Starý název"
    old.mkdir(parents=True)
    f = silent_m4a_factory(old / "01.m4a")
    w, (a,) = _book(db_session, [f], final_path=old, title="Stary nazev")
    new = tmp_path / "shelf" / "Karel Capek - (1936) Valka s mloky (cte Jan Novak, CRo 2010)"
    old.rename(new)
    pathfollow.follow_paths(db_session, [tmp_path / "shelf"], apply=True)
    from audiobiblio.library.pipelines.auto_finalize import _resolved_value
    assert _resolved_value(db_session, "work", w.id, "final_path") == str(new)
    assert _resolved_value(db_session, "work", w.id, "title") == "Valka s mloky"
    assert _resolved_value(db_session, "work", w.id, "author") == "Karel Capek"
    assert _resolved_value(db_session, "episode", a.episode_id, "narrator") == "Jan Novak"
    assert _resolved_value(db_session, "work", w.id, "publisher") == "CRo 2010"


# --- the nightly job must survive a locked database ----------------------------

def _locked() -> OperationalError:
    return OperationalError("UPDATE assets ...", {}, Exception("database is locked"))


def _unfingerprinted(db, files):
    book, assets = _book(db, files)
    for a in assets:                        # _book fingerprints; undo that
        a.extra = {}
    db.commit()
    return assets


def test_with_retry_waits_and_succeeds(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(pathfollow, "_sleep", sleeps.append)

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise _locked()
        return "ok"
    assert pathfollow._with_retry(flaky) == "ok"
    assert sleeps == [2, 4]


def test_with_retry_does_not_hide_other_errors(monkeypatch):
    monkeypatch.setattr(pathfollow, "_sleep", lambda s: None)

    def broken():
        raise OperationalError("x", {}, Exception("no such table: assets"))
    with pytest.raises(OperationalError):
        pathfollow._with_retry(broken)


def test_backfill_survives_a_transient_lock(db_session, silent_m4a_factory, tmp_path, monkeypatch):
    files = [silent_m4a_factory(tmp_path / f"{n}.m4a") for n in range(3)]
    for n, f in enumerate(files[1:], 1):
        _alter_audio(f) if n else None
    assets = _unfingerprinted(db_session, files)
    monkeypatch.setattr(pathfollow, "_sleep", lambda s: None)
    monkeypatch.setattr(pathfollow, "_BATCH", 2)
    real, state = pathfollow._write_fingerprints, {"fail": 1}

    def flaky(session, rows):
        if state["fail"]:
            state["fail"] -= 1
            raise _locked()
        return real(session, rows)
    monkeypatch.setattr(pathfollow, "_write_fingerprints", flaky)
    assert pathfollow.backfill_fingerprints(db_session) == 3
    db_session.expire_all()
    assert all((a.extra or {}).get("fp") for a in assets)


def test_backfill_never_raises_on_a_persistent_lock_and_resumes(
        db_session, silent_m4a_factory, tmp_path, monkeypatch):
    files = [silent_m4a_factory(tmp_path / f"{n}.m4a") for n in range(2)]
    _alter_audio(files[1])
    assets = _unfingerprinted(db_session, files)
    monkeypatch.setattr(pathfollow, "_sleep", lambda s: None)
    real = pathfollow._write_fingerprints

    def always_locked(session, rows):
        raise _locked()
    monkeypatch.setattr(pathfollow, "_write_fingerprints", always_locked)
    assert pathfollow.backfill_fingerprints(db_session) == 0     # no exception
    db_session.expire_all()
    assert not any((a.extra or {}).get("fp") for a in assets)
    monkeypatch.setattr(pathfollow, "_write_fingerprints", real)  # lock gone
    assert pathfollow.backfill_fingerprints(db_session) == 2


def test_backfill_does_not_bump_updated_at(db_session, silent_m4a_factory, tmp_path):
    """A fingerprint is bookkeeping, not an edit: other code reads updated_at."""
    f = silent_m4a_factory(tmp_path / "a.m4a")
    (a,) = _unfingerprinted(db_session, [f])
    before = a.updated_at
    pathfollow.backfill_fingerprints(db_session)
    db_session.refresh(a)
    assert (a.extra or {}).get("fp") and a.updated_at == before
