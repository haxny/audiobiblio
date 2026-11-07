"""Migration d8e2f4a6b1c3 upgrades a pre-health database (and is
idempotent when init_db's create_all got there first)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from audiobiblio.core.db.models import Base

ROOT = Path(__file__).resolve().parents[2]


def _cfg() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _old_schema(db: Path) -> None:
    eng = sa.create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.exec_driver_sql("DROP TABLE crawl_runs")
        c.exec_driver_sql("DROP TABLE health_snapshots")
        c.exec_driver_sql("DROP INDEX ix_episodes_playable_till")
        c.exec_driver_sql("ALTER TABLE episodes DROP COLUMN playable_till")
    eng.dispose()


def _schema(db: Path):
    eng = sa.create_engine(f"sqlite:///{db}")
    insp = sa.inspect(eng)
    out = (set(insp.get_table_names()),
           {c["name"] for c in insp.get_columns("episodes")})
    eng.dispose()
    return out


def test_upgrade_from_previous_head_and_back(tmp_path):
    db = tmp_path / "db.sqlite3"
    _old_schema(db)
    with patch("audiobiblio.core.db.session.default_db_path", return_value=db):
        command.stamp(_cfg(), "c7d1e2f3a4b5")
        command.upgrade(_cfg(), "head")
        tables, cols = _schema(db)
        assert {"crawl_runs", "health_snapshots"} <= tables
        assert "playable_till" in cols
        command.downgrade(_cfg(), "c7d1e2f3a4b5")
        tables, cols = _schema(db)
        assert "crawl_runs" not in tables and "playable_till" not in cols


def test_upgrade_is_idempotent_after_create_all(tmp_path):
    db = tmp_path / "db.sqlite3"
    eng = sa.create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    with patch("audiobiblio.core.db.session.default_db_path", return_value=db):
        command.stamp(_cfg(), "c7d1e2f3a4b5")
        command.upgrade(_cfg(), "head")
