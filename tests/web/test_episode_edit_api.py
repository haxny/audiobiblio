"""Tests for PATCH /api/v1/episodes/{id}/metadata — manual metadata editing.

TDD: tests written before implementation (RED phase).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import Episode, FieldOrigin, MetadataValue, Work
from audiobiblio.web.deps import get_db


@pytest.fixture()
def ep_client(db_session, episode_factory):
    """Minimal test app with only the episodes router."""
    from audiobiblio.web.routers import episodes

    app = FastAPI()
    app.include_router(episodes.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# PATCH /api/v1/episodes/{id}/metadata — happy path
# ---------------------------------------------------------------------------


def test_patch_title_updates_orm_and_provenance(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "title", "value": "My Manual Title"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["field"] == "title"
    assert data["value"] == "My Manual Title"
    assert data["origin"] == "manual"
    assert data["applied"] is True

    db_session.expire(ep)
    assert ep.title == "My Manual Title"

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="title",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None
    assert mv.value == "My Manual Title"
    assert mv.source == "user"


def test_patch_description_maps_to_summary(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "description", "value": "A rich summary."},
    )
    assert r.status_code == 200
    assert r.json()["applied"] is True
    db_session.expire(ep)
    assert ep.summary == "A rich summary."

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="description",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None


def test_patch_author_updates_work_column(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "author", "value": "Jane Doe"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] is True
    assert data["origin"] == "manual"

    db_session.expire(ep.work)
    assert ep.work.author == "Jane Doe"

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="work",
            entity_id=ep.work_id,
            field="author",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None
    assert mv.value == "Jane Doe"


def test_patch_year_updates_work_column(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "year", "value": "2023"},
    )
    assert r.status_code == 200
    assert r.json()["applied"] is True

    db_session.expire(ep.work)
    assert ep.work.year == 2023

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="work",
            entity_id=ep.work_id,
            field="year",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None
    assert mv.value == "2023"


def test_patch_narrator_provenance_only_no_orm_column(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "narrator", "value": "John Smith"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] is False
    assert data["origin"] == "manual"

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="narrator",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None
    assert mv.value == "John Smith"


def test_patch_genre_provenance_only_no_orm_column(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "genre", "value": "Drama"},
    )
    assert r.status_code == 200
    assert r.json()["applied"] is False

    mv = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="genre",
            origin=FieldOrigin.MANUAL,
        )
        .first()
    )
    assert mv is not None


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_patch_unknown_field_returns_400(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "unknown_field", "value": "x"},
    )
    assert r.status_code == 400


def test_patch_empty_value_returns_422(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "title", "value": ""},
    )
    assert r.status_code == 422


def test_patch_whitespace_only_value_returns_422(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "title", "value": "   "},
    )
    assert r.status_code == 422


def test_patch_episode_not_found_returns_404(ep_client):
    r = ep_client.patch(
        "/api/v1/episodes/99999/metadata",
        json={"field": "title", "value": "x"},
    )
    assert r.status_code == 404


def test_patch_year_non_integer_returns_422(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "year", "value": "not_a_year"},
    )
    assert r.status_code == 422


def test_patch_year_float_returns_422(ep_client, db_session, episode_factory):
    ep = episode_factory()
    r = ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "year", "value": "2023.5"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Upsert semantics: second edit must update existing MANUAL row, not add new
# ---------------------------------------------------------------------------


def test_second_edit_upserts_manual_row(ep_client, db_session, episode_factory):
    ep = episode_factory()
    ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "title", "value": "First Title"},
    )
    ep_client.patch(
        f"/api/v1/episodes/{ep.id}/metadata",
        json={"field": "title", "value": "Second Title"},
    )
    rows = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="title",
            origin=FieldOrigin.MANUAL,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].value == "Second Title"
    db_session.expire(ep)
    assert ep.title == "Second Title"


# ---------------------------------------------------------------------------
# Ingest guard: MANUAL values survive scraped re-ingest
# ---------------------------------------------------------------------------


def test_manual_title_survives_scraped_reingest(db_session, episode_factory):
    """PATCH title → re-ingest with different scraped title → ORM title stays manual
    AND a SCRAPED row exists alongside the MANUAL row.
    """
    from audiobiblio.core.provenance import record_value
    from audiobiblio.library.pipelines.ingest import upsert_from_item

    ep = episode_factory()
    # Simulate what the PATCH endpoint does
    record_value(
        db_session, "episode", ep.id, "title", "Manual Title", FieldOrigin.MANUAL, "user"
    )
    ep.title = "Manual Title"
    db_session.commit()

    # Re-ingest with a different scraped title — should NOT overwrite
    upsert_from_item(
        db_session,
        url=ep.url,
        item_title="Completely Different Scraped Title",
        series_name=None,
        author=None,
        uploader=None,
        ext_id=ep.ext_id,
    )

    db_session.expire(ep)
    # ORM column must still hold the manual value
    assert ep.title == "Manual Title"

    # SCRAPED row must exist (observation recorded even though it loses)
    scraped = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="title",
            origin=FieldOrigin.SCRAPED,
        )
        .first()
    )
    assert scraped is not None
    assert scraped.value == "Completely Different Scraped Title"


def test_manual_author_survives_scraped_reingest(db_session, episode_factory):
    """PATCH author → re-ingest with different scraped author → work.author stays manual."""
    from audiobiblio.core.provenance import record_value
    from audiobiblio.library.pipelines.ingest import upsert_from_item

    ep = episode_factory()
    work_id = ep.work_id

    record_value(
        db_session, "work", work_id, "author", "Manual Author", FieldOrigin.MANUAL, "user"
    )
    ep.work.author = "Manual Author"
    db_session.commit()

    upsert_from_item(
        db_session,
        url=ep.url,
        item_title=ep.title,
        series_name=None,
        author="Scraped Different Author",
        uploader=None,
        ext_id=ep.ext_id,
    )

    db_session.expire(ep.work)
    assert ep.work.author == "Manual Author"

    scraped = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="work",
            entity_id=work_id,
            field="author",
            origin=FieldOrigin.SCRAPED,
        )
        .first()
    )
    assert scraped is not None
    assert scraped.value == "Scraped Different Author"
