"""metadata.json carries what audiobiblio last saw in a book folder.

The user edits folders and tags in foobar / cmdr, which will never write our
bookkeeping. So audiobiblio records its own view next to the audio (key
"audiobiblio"); any later difference between that record and the disk is the
signal to look at the real files and update the DB. This module only WRITES the
record (reconcile comes later) and never touches other keys ABS or the user put
into the file.
"""
from __future__ import annotations

import json
from pathlib import Path

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, Program, Series, Station, Work,
)
from audiobiblio.library import abs_metadata, manifest, pathfollow


def _book(db, folder: Path, files: list[Path], title="Kniha"):
    st = db.query(Station).first() or Station(code="x", name="X")
    db.add(st)
    db.flush()
    p = Program(station_id=st.id, name=f"P{db.query(Program).count()}")
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name=p.name)
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=title, serial_uuid="77bd5bc8-7870-395d-a8c1-753f6e51bc69")
    db.add(w)
    db.flush()
    assets = []
    for n, f in enumerate(files, 1):
        ep = Episode(work_id=w.id, title=f"d{n}", episode_number=n)
        db.add(ep)
        db.flush()
        a = Asset(episode_id=ep.id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE,
                  file_path=str(f), extra={"tag_snapshot": {"title": f"Dil {n}", "narrator": "Jan Novak"}})
        db.add(a)
        db.flush()
        assets.append(a)
    db.commit()
    return w, assets


def _folder(tmp_path, silent_m4a_factory, names=("01.m4a", "02.m4a")):
    d = tmp_path / "shelf" / "Karel Capek - Valka s mloky"
    d.mkdir(parents=True)
    files = [silent_m4a_factory(d / n) for n in names]
    return d, files


def test_manifest_describes_the_folder(db_session, silent_m4a_factory, tmp_path):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, _a = _book(db_session, d, files)
    m = manifest.build_manifest(db_session, w, d)
    assert m["schema"] == 1 and m["work_id"] == w.id
    assert m["serial_uuid"] == w.serial_uuid
    assert [f["name"] for f in m["files"]] == ["01.m4a", "02.m4a"]
    f1 = m["files"][0]
    assert f1["part"] == 1 and f1["size"] > 0 and f1["fp"] and f1["mtime"] > 0
    assert f1["tags"] == {"title": "Dil 1", "narrator": "Jan Novak"}


def test_guid_is_stable_and_unique_per_book(db_session, silent_m4a_factory, tmp_path):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, _a = _book(db_session, d, files)
    d2, files2 = _folder(tmp_path / "x", silent_m4a_factory)
    w2, _b = _book(db_session, d2, files2, title="Jina")
    g1 = manifest.work_guid(db_session, w)
    assert manifest.work_guid(db_session, w) == g1
    assert manifest.work_guid(db_session, w2) != g1


def test_write_keeps_foreign_keys_and_is_idempotent(db_session, silent_m4a_factory, tmp_path):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, _a = _book(db_session, d, files)
    (d / "metadata.json").write_text(json.dumps(
        {"title": "Valka s mloky", "chapters": [{"id": 0}], "tags": ["a"]}))
    assert manifest.write_manifest(db_session, w, d) is True
    data = json.loads((d / "metadata.json").read_text())
    assert data["title"] == "Valka s mloky" and data["chapters"] == [{"id": 0}]   # ABS's keys intact
    assert data["audiobiblio"]["guid"] and len(data["audiobiblio"]["files"]) == 2
    assert "written_at" in data["audiobiblio"]
    assert manifest.write_manifest(db_session, w, d) is False                      # nothing changed


def test_a_renamed_file_changes_the_record(db_session, silent_m4a_factory, tmp_path):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, assets = _book(db_session, d, files)
    manifest.write_manifest(db_session, w, d)
    new = files[0].rename(d / "Dil 1.m4a")
    assets[0].file_path = str(new)                  # (pathfollow would do this)
    db_session.commit()
    assert manifest.write_manifest(db_session, w, d) is True
    names = [f["name"] for f in json.loads((d / "metadata.json").read_text())["audiobiblio"]["files"]]
    assert names == ["02.m4a", "Dil 1.m4a"] or names == ["Dil 1.m4a", "02.m4a"]


def test_shared_folder_is_not_stamped(db_session, silent_m4a_factory, tmp_path):
    """A folder holding several works (a collection) has no single book identity."""
    d, files = _folder(tmp_path, silent_m4a_factory)
    from audiobiblio.core.db.models import FieldOrigin
    from audiobiblio.core.provenance import record_value
    w, _a = _book(db_session, d, files[:1])
    w2, _b = _book(db_session, d, files[1:], title="Druha kniha")
    for x in (w, w2):                      # shelved into the same folder = a collection
        record_value(db_session, "work", x.id, "final_path", str(d), FieldOrigin.MANUAL, "t")
    db_session.commit()
    assert manifest.write_manifest(db_session, w, d) is False
    assert not (d / "metadata.json").exists()


def test_publish_to_abs_writes_the_record_too(db_session, silent_m4a_factory, tmp_path, monkeypatch):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, _a = _book(db_session, d, files)
    monkeypatch.setattr(abs_metadata, "mark_library_dirty", lambda f: None)
    abs_metadata.publish_to_abs(db_session, w, d)
    assert "audiobiblio" in json.loads((d / "metadata.json").read_text())


def test_needs_write_is_a_pure_dry_run(db_session, silent_m4a_factory, tmp_path):
    d, files = _folder(tmp_path, silent_m4a_factory)
    w, _a = _book(db_session, d, files)
    assert manifest.needs_write(db_session, w, d) is True
    assert not (d / "metadata.json").exists()                       # no file written
    from audiobiblio.library.pipelines.auto_finalize import _resolved_value
    assert _resolved_value(db_session, "work", w.id, "guid") is None  # no guid created
    manifest.write_manifest(db_session, w, d)
    assert manifest.needs_write(db_session, w, d) is False
