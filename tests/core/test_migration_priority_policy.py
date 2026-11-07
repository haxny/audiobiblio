"""Migration d8e9f0a1b2c3: priority-policy columns, idempotent re-run."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def _cfg(monkeypatch, db_file: Path) -> Config:
    from audiobiblio.core.db import session as session_mod
    real = session_mod.get_engine
    monkeypatch.setattr(session_mod, "get_engine",
                        lambda db_url=None: real(f"sqlite:///{db_file}"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _cols(db_file: Path, table: str) -> set[str]:
    eng = sa.create_engine(f"sqlite:///{db_file}")
    try:
        return {c["name"] for c in sa.inspect(eng).get_columns(table)}
    finally:
        eng.dispose()


def test_upgrade_adds_columns_and_downgrade_round_trips(tmp_path, monkeypatch):
    db = tmp_path / "m.sqlite3"
    cfg = _cfg(monkeypatch, db)
    command.upgrade(cfg, "c7d1e2f3a4b5")
    assert "content_class" not in _cols(db, "crawl_targets")
    command.upgrade(cfg, "d8e9f0a1b2c3")
    assert {"content_class", "priority_base"} <= _cols(db, "crawl_targets")
    assert "playable_till" in _cols(db, "episodes")
    command.downgrade(cfg, "c7d1e2f3a4b5")
    assert "content_class" not in _cols(db, "crawl_targets")
    command.upgrade(cfg, "d8e9f0a1b2c3")  # playable_till kept → no duplicate-column error
    assert "content_class" in _cols(db, "crawl_targets")
