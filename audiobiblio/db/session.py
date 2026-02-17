from __future__ import annotations
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base
from ..paths import get_dirs

def default_db_path() -> Path:
    dirs = get_dirs()
    # Keep DB inside data dir
    return dirs["data"] / "db.sqlite3"

def get_engine(db_url: str | None = None):
    if not db_url:
        db_file = default_db_path()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{db_file}"
    # sqlite pragmas: WAL for fewer locks on NAS; safe defaults elsewhere
    engine = create_engine(
        db_url,
        future=True,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    if db_url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            conn.exec_driver_sql("PRAGMA foreign_keys=ON;")
    return engine

def init_db(db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)

def get_session(db_url: str | None = None):
    engine = get_engine(db_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()