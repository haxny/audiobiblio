"""/works/{id} — the one page per book: parts in reading order, audio
status, inline player, completeness (user: 'kde to najdu?')."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Base, Episode, Program, Series, Station,
    Work,
)
from audiobiblio.web.deps import get_db


@pytest.fixture()
def db_session():
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
def book(db_session):
    st = Station(code="CRo2", name="Dvojka")
    db_session.add(st)
    db_session.flush()
    prog = Program(station_id=st.id, name="Četba na pokračování")
    db_session.add(prog)
    db_session.flush()
    ser = Series(program_id=prog.id, name="Četba na pokračování")
    db_session.add(ser)
    db_session.flush()
    work = Work(series_id=ser.id, title="Testovací kniha", author="Jan Autor")
    db_session.add(work)
    db_session.flush()
    # Parts inserted OUT of order — the page must sort by episode_number.
    for n, status in [(2, AssetStatus.COMPLETE), (1, AssetStatus.COMPLETE), (3, None)]:
        ep = Episode(work_id=work.id, title=f"Testovací kniha díl {n}", episode_number=n)
        db_session.add(ep)
        db_session.flush()
        if status:
            db_session.add(Asset(
                episode_id=ep.id, type=AssetType.AUDIO,
                status=status, file_path=f"/media/x/{n}.m4a"))
    db_session.flush()
    return work


@pytest.fixture()
def client(db_session):
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=True)


class TestWorkDetail:
    def test_returns_200(self, client, book):
        assert client.get(f"/works/{book.id}").status_code == 200

    def test_404_unknown(self, client, book):
        assert client.get("/works/99999").status_code == 404

    def test_shows_title_author_and_completeness(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert "Testovací kniha" in t
        assert "Jan Autor" in t
        assert "2/3 dílů staženo" in t

    def test_parts_in_reading_order(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert t.index("díl 1") < t.index("díl 2") < t.index("díl 3")

    def test_player_and_program_breadcrumb(self, client, book):
        t = client.get(f"/works/{book.id}").text
        assert "work-player" in t
        assert "Četba na pokračování (CRo2)" in t

    def test_route_registered(self):
        from audiobiblio.web.views import router as views_router
        paths = [getattr(r, "path", None) for r in views_router.routes]
        assert "/works/{work_id}" in paths
