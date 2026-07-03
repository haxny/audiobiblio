import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Base, Episode, Program, Series, Station, Work,
)

# Register shared audio-fixture so it is available in all test modules.
from tests.fixtures_util import silent_m4a  # noqa: F401


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def episode_factory(db_session):
    """Create Episode with full Station->Program->Series->Work hierarchy.

    Episodes made with the same program_name share one Program (needed for
    the per-program approval-threshold tests).

    Note: plan_downloads() auto-creates required Assets (META_JSON, WEBPAGE, AUDIO)
    via ensure_assets_for_episode(), so we don't pre-create them here.
    """
    counter = {"n": 0}

    def make(program_name: str = "Prog") -> Episode:
        counter["n"] += 1
        n = counter["n"]
        station = db_session.query(Station).filter_by(code="tst").one_or_none()
        if station is None:
            station = Station(code="tst", name="Test Station")
            db_session.add(station)
            db_session.flush()
        program = db_session.query(Program).filter_by(name=program_name).one_or_none()
        if program is None:
            program = Program(station_id=station.id, name=program_name)
            db_session.add(program)
            db_session.flush()
        series_name = f"{program_name} S"
        series = db_session.query(Series).filter_by(
            program_id=program.id, name=series_name).one_or_none()
        if series is None:
            series = Series(program_id=program.id, name=series_name)
            db_session.add(series)
            db_session.flush()
        work = Work(series_id=series.id, title=f"Work {n}")
        db_session.add(work)
        db_session.flush()
        ep = Episode(work_id=work.id, title=f"Episode {n}", ext_id=f"ext-{n}",
                     url=f"https://example.cz/ep-{n}")
        db_session.add(ep)
        db_session.flush()
        return ep

    return make
