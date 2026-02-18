"""
deps — FastAPI dependencies (DB session, config).
"""
from __future__ import annotations
from typing import Generator
from sqlalchemy.orm import Session, sessionmaker
from ..db.session import get_engine


_SessionLocal: sessionmaker | None = None


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a DB session, closes after request."""
    factory = _get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
