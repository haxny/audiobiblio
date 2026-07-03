import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import Base
from audiobiblio.web.deps import get_db


@pytest.fixture()
def db_session():
    """Override db_session for web tests.

    StaticPool ensures all connections share the same underlying SQLite
    in-memory connection — required so TestClient threads and the test
    fixture see the same database.  check_same_thread=False allows the
    session created here to be handed off to TestClient worker threads.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def client(db_session):
    """Test app with routers only — create_app() would start the scheduler."""
    from audiobiblio.web.routers import jobs, targets, upgrades

    app = FastAPI()
    app.include_router(targets.router)
    app.include_router(jobs.router)
    app.include_router(upgrades.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)
