"""
Tests for provenance.record_value (Phase 4 Task 3).

Five cases:
1. insert-then-update on same key — observed_at moves forward, exactly one row exists.
2. distinct sources coexist — two rows created, one per source.
3. ingest creates SCRAPED rows for a new episode.
4. re-ingest updates existing rows, does not create duplicates.
5. provenance failure is isolated — monkeypatched record_value raises, upsert still succeeds.
"""
from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import Base, FieldOrigin, MetadataValue
from audiobiblio.core.provenance import record_value


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = factory()
    yield s
    s.close()
    engine.dispose()


def _count_rows(session) -> int:
    return session.query(MetadataValue).count()


# ---------------------------------------------------------------------------
# Case 1: insert-then-update on same key
# ---------------------------------------------------------------------------


def test_insert_then_update_same_key_single_row(session):
    """Calling record_value twice with the same key produces exactly one row;
    observed_at is updated on the second call."""
    mv1 = record_value(
        session,
        entity_type="episode",
        entity_id=1,
        field="title",
        value="First Title",
        origin=FieldOrigin.SCRAPED,
        source="test-source",
    )
    session.flush()
    first_observed = mv1.observed_at
    assert _count_rows(session) == 1

    # Small sleep so observed_at can advance (datetime.utcnow granularity)
    time.sleep(0.01)

    mv2 = record_value(
        session,
        entity_type="episode",
        entity_id=1,
        field="title",
        value="Updated Title",
        origin=FieldOrigin.SCRAPED,
        source="test-source",
    )
    session.flush()

    assert _count_rows(session) == 1, "second call must update, not insert"
    assert mv2.value == "Updated Title"
    assert mv2.observed_at >= first_observed


# ---------------------------------------------------------------------------
# Case 2: distinct sources coexist
# ---------------------------------------------------------------------------


def test_distinct_sources_produce_separate_rows(session):
    """Two calls with different source strings create two independent rows."""
    record_value(
        session,
        entity_type="episode",
        entity_id=1,
        field="title",
        value="From Source A",
        origin=FieldOrigin.SCRAPED,
        source="source-a",
    )
    record_value(
        session,
        entity_type="episode",
        entity_id=1,
        field="title",
        value="From Source B",
        origin=FieldOrigin.SCRAPED,
        source="source-b",
    )
    session.flush()

    assert _count_rows(session) == 2
    values = {r.source: r.value for r in session.query(MetadataValue).all()}
    assert values["source-a"] == "From Source A"
    assert values["source-b"] == "From Source B"


# ---------------------------------------------------------------------------
# Case 3: ingest creates SCRAPED rows for a new episode
# ---------------------------------------------------------------------------


def test_ingest_creates_scraped_rows_for_new_episode(db_session):
    """upsert_from_item must write SCRAPED MetadataValue rows for title, description,
    work author, and work title."""
    from audiobiblio.library.pipelines.ingest import upsert_from_item

    ep, work = upsert_from_item(
        db_session,
        url="https://example.cz/ep-prov-new",
        item_title="Konkrétní název",
        series_name="Prov Seriál",
        author="Jan Novák",
        uploader="mujrozhlas",
        episode_number=1,
        ext_id="ep-prov-001",
        summary="Stručný popis epizody",
        discovery_source="mujrozhlas",
    )

    rows = db_session.query(MetadataValue).filter_by(origin=FieldOrigin.SCRAPED).all()
    by_field = {(r.entity_type, r.field): r for r in rows}

    assert ("episode", "title") in by_field, "episode title must be recorded"
    assert by_field[("episode", "title")].value == "Konkrétní název"
    assert by_field[("episode", "title")].source == "mujrozhlas"

    assert ("episode", "description") in by_field, "episode description must be recorded"
    assert by_field[("episode", "description")].value == "Stručný popis epizody"

    assert ("work", "author") in by_field, "work author must be recorded"
    assert by_field[("work", "author")].value == "Jan Novák"

    assert ("work", "title") in by_field, "work title must be recorded"


# ---------------------------------------------------------------------------
# Case 4: re-ingest updates, not duplicates
# ---------------------------------------------------------------------------


def test_reingest_updates_not_duplicates(db_session):
    """Calling upsert_from_item twice for the same episode must update existing
    MetadataValue rows, not insert new ones."""
    from audiobiblio.library.pipelines.ingest import upsert_from_item

    upsert_from_item(
        db_session,
        url="https://example.cz/ep-reingest",
        item_title="Původní název",
        series_name="Reingest Seriál",
        author="Autor Jeden",
        uploader="mujrozhlas",
        episode_number=2,
        ext_id="ep-reingest-prov",
        discovery_source="mujrozhlas",
    )
    count_after_first = db_session.query(MetadataValue).count()

    upsert_from_item(
        db_session,
        url="https://example.cz/ep-reingest",
        item_title="Původní název",
        series_name="Reingest Seriál",
        author="Autor Jeden",
        uploader="mujrozhlas",
        episode_number=2,
        ext_id="ep-reingest-prov",
        discovery_source="mujrozhlas",
    )
    count_after_second = db_session.query(MetadataValue).count()

    assert count_after_second == count_after_first, (
        "re-ingest must update existing rows, not create duplicates"
    )


# ---------------------------------------------------------------------------
# Case 5: provenance failure is isolated
# ---------------------------------------------------------------------------


def test_provenance_failure_does_not_break_ingest(db_session):
    """If record_value raises, upsert_from_item must still return a valid episode."""
    from audiobiblio.library.pipelines.ingest import upsert_from_item

    with patch(
        "audiobiblio.library.pipelines.ingest.record_value",
        side_effect=RuntimeError("simulated provenance failure"),
    ):
        ep, work = upsert_from_item(
            db_session,
            url="https://example.cz/ep-isolated",
            item_title="Izolovaná epizoda",
            series_name="Isolated Seriál",
            author=None,
            uploader="mujrozhlas",
            episode_number=9,
            ext_id="ep-isolated-001",
        )

    assert ep is not None
    assert ep.id is not None
    assert ep.title == "Izolovaná epizoda"
