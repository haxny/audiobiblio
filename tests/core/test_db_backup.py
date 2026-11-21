"""Scheduled backup of the SQLite DB: consistent copy, checked, compressed, pruned.

The DB (~1 GB) holds all provenance and every manual edit; before this only
hand-made copies existed. The backup is a LOGICAL safety net (mistaken bulk
edit, corrupt page); it lives on the same volume, so it does not replace an
off-box backup (Hyper Backup).
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from collections import namedtuple
from datetime import datetime

import pytest

from audiobiblio.core import db_backup


def _make_db(path, rows=50):
    con = sqlite3.connect(path)
    con.execute("create table t (id integer primary key, v text)")
    con.executemany("insert into t(v) values (?)", [(f"row {i}",) for i in range(rows)])
    con.commit()
    con.close()


def _rows(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("select count(*) from t").fetchone()[0]
    finally:
        con.close()


def test_backup_is_a_checked_compressed_copy(tmp_path):
    src = tmp_path / "db.sqlite3"
    _make_db(src)
    res = db_backup.backup_database(src, tmp_path / "backups", keep=3,
                                    now=datetime(2026, 11, 21, 4, 50, 0))
    assert res.path.name == "db-20261121-045000.sqlite3.gz" and res.size_bytes > 0
    restored = tmp_path / "restored.sqlite3"
    with gzip.open(res.path, "rb") as f, open(restored, "wb") as out:
        shutil.copyfileobj(f, out)
    assert _rows(restored) == 50
    assert [p.name for p in (tmp_path / "backups").iterdir()] == [res.path.name]   # no temp left


def test_backup_of_a_live_wal_database_is_consistent(tmp_path):
    src = tmp_path / "db.sqlite3"
    _make_db(src)
    writer = sqlite3.connect(src)
    writer.execute("pragma journal_mode=wal")
    writer.execute("insert into t(v) values ('late')")
    writer.commit()                                  # sits in the WAL, writer still open
    res = db_backup.backup_database(src, tmp_path / "b", keep=3)
    restored = tmp_path / "r.sqlite3"
    with gzip.open(res.path, "rb") as f, open(restored, "wb") as out:
        shutil.copyfileobj(f, out)
    writer.close()
    assert _rows(restored) == 51


def test_prune_keeps_only_the_newest(tmp_path):
    src = tmp_path / "db.sqlite3"
    _make_db(src)
    b = tmp_path / "b"
    b.mkdir()
    for d in (1, 2, 3, 4):
        (b / f"db-2026110{d}-045000.sqlite3.gz").write_bytes(b"old")
    (b / "notes.txt").write_text("not a backup")      # foreign files are never touched
    res = db_backup.backup_database(src, b, keep=3, now=datetime(2026, 11, 21, 4, 50))
    names = sorted(p.name for p in b.iterdir())
    assert names == ["db-20261103-045000.sqlite3.gz", "db-20261104-045000.sqlite3.gz",
                     "db-20261121-045000.sqlite3.gz", "notes.txt"]
    assert sorted(p.name for p in res.pruned) == ["db-20261101-045000.sqlite3.gz",
                                                  "db-20261102-045000.sqlite3.gz"]


def test_refuses_when_there_is_not_enough_space(tmp_path, monkeypatch):
    src = tmp_path / "db.sqlite3"
    _make_db(src)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: usage(10**12, 10**12 - 10, 10))
    with pytest.raises(db_backup.BackupSkipped):
        db_backup.backup_database(src, tmp_path / "b")
    assert not list((tmp_path / "b").glob("*"))      # nothing left behind


def test_failed_check_leaves_no_file_and_keeps_old_backups(tmp_path, monkeypatch):
    src = tmp_path / "db.sqlite3"
    _make_db(src)
    b = tmp_path / "b"
    b.mkdir()
    old = b / "db-20261101-045000.sqlite3.gz"
    old.write_bytes(b"old")
    monkeypatch.setattr(db_backup, "_quick_check", lambda p: "database disk image is malformed")
    with pytest.raises(db_backup.BackupError):
        db_backup.backup_database(src, b, keep=1, now=datetime(2026, 11, 21, 4, 50))
    assert [p.name for p in b.iterdir()] == [old.name]   # no partial file, nothing pruned


def test_verify_backup_detects_a_truncated_archive(tmp_path):
    src = tmp_path / "db.sqlite3"
    _make_db(src, rows=500)
    res = db_backup.backup_database(src, tmp_path / "b")
    assert db_backup.verify_backup(res.path) is True
    data = res.path.read_bytes()
    res.path.write_bytes(data[: len(data) // 2])
    assert db_backup.verify_backup(res.path) is False
