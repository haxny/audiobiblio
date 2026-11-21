"""The nightly DB backup job: writes, verifies, and never takes the scheduler down."""
from __future__ import annotations

import sqlite3

from audiobiblio.acquire import scheduler
from audiobiblio.core import db_backup


def _db(path):
    con = sqlite3.connect(path)
    con.execute("create table t(x)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()


def test_job_writes_a_verified_backup(monkeypatch, tmp_path):
    db = tmp_path / "db.sqlite3"
    _db(db)
    monkeypatch.setattr("audiobiblio.core.db.session.default_db_path", lambda: db)
    monkeypatch.setattr("audiobiblio.paths.get_dirs", lambda: {"data": tmp_path})
    scheduler._db_backup_job()
    files = list((tmp_path / "backups").glob("db-*.sqlite3.gz"))
    assert len(files) == 1 and db_backup.verify_backup(files[0])


def test_job_swallows_a_skip_and_an_error(monkeypatch, tmp_path):
    db = tmp_path / "db.sqlite3"
    _db(db)
    monkeypatch.setattr("audiobiblio.core.db.session.default_db_path", lambda: db)
    monkeypatch.setattr("audiobiblio.paths.get_dirs", lambda: {"data": tmp_path})

    def skip(*a, **k):
        raise db_backup.BackupSkipped("no space")
    monkeypatch.setattr(db_backup, "backup_database", skip)
    scheduler._db_backup_job()                      # warning, no exception

    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(db_backup, "backup_database", boom)
    scheduler._db_backup_job()                      # error logged, no exception
