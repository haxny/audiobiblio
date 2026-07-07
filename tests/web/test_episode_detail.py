"""Tests for the episode detail page (Task 8).

Covers:
- GET /api/v1/episodes/{id}/audio — preview-player file streaming endpoint.
- _episode_metadata_rows(db, ep) — provenance rows + resolved winner per field
  (grouping-function-level test, same pattern as tests/web/test_inbox_view.py).
- /episodes list links to /episodes/{id} + route census.

TDD: written before implementation (RED phase).
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, FieldOrigin, JobStatus,
    MetadataValue,
)
from audiobiblio.web.deps import get_db


@pytest.fixture()
def ep_client(db_session):
    """Minimal test app with only the episodes router."""
    from audiobiblio.web.routers import episodes

    app = FastAPI()
    app.include_router(episodes.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


@pytest.fixture()
def views_client(db_session):
    """Test app with only the views (HTML) router mounted.

    Safe here: the episode-detail and episodes-list routes don't touch
    load_config() or the scheduler.
    """
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _add_audio_asset(
    db_session,
    ep,
    file_path: str | None,
    status: AssetStatus = AssetStatus.COMPLETE,
) -> Asset:
    asset = Asset(
        episode_id=ep.id,
        type=AssetType.AUDIO,
        status=status,
        file_path=file_path,
        size_bytes=1234,
        bitrate=128_000,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


# ---------------------------------------------------------------------------
# GET /api/v1/episodes/{id}/audio
# ---------------------------------------------------------------------------


def test_audio_endpoint_serves_m4a_with_audio_mp4(
    ep_client, db_session, episode_factory, silent_m4a
):
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(silent_m4a))
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mp4"
    assert len(r.content) == silent_m4a.stat().st_size


def test_audio_endpoint_serves_mp3_with_audio_mpeg(
    ep_client, db_session, episode_factory, tmp_path
):
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"ID3fake-mp3-payload")
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(mp3))
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"


def test_audio_endpoint_unknown_suffix_falls_back_to_octet_stream(
    ep_client, db_session, episode_factory, tmp_path
):
    blob = tmp_path / "episode.weird"
    blob.write_bytes(b"data")
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(blob))
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"


def test_audio_endpoint_404_when_no_audio_asset(ep_client, db_session, episode_factory):
    ep = episode_factory()
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 404


def test_audio_endpoint_404_when_asset_missing_status(
    ep_client, db_session, episode_factory, tmp_path
):
    f = tmp_path / "there.m4a"
    f.write_bytes(b"x")
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(f), status=AssetStatus.MISSING)
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 404


def test_audio_endpoint_404_when_file_gone_from_disk(
    ep_client, db_session, episode_factory, tmp_path
):
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(tmp_path / "vanished.m4a"))
    db_session.commit()

    r = ep_client.get(f"/api/v1/episodes/{ep.id}/audio")

    assert r.status_code == 404


def test_audio_endpoint_404_when_episode_unknown(ep_client):
    r = ep_client.get("/api/v1/episodes/99999/audio")
    assert r.status_code == 404


def test_audio_endpoint_supports_range_requests(
    ep_client, db_session, episode_factory, tmp_path
):
    """Starlette FileResponse handles HTTP Range → seeking works in <audio>."""
    mp3 = tmp_path / "seekable.mp3"
    mp3.write_bytes(b"0123456789" * 10)
    ep = episode_factory()
    _add_audio_asset(db_session, ep, str(mp3))
    db_session.commit()

    r = ep_client.get(
        f"/api/v1/episodes/{ep.id}/audio", headers={"Range": "bytes=10-19"}
    )

    assert r.status_code == 206
    assert r.content == b"0123456789"


# ---------------------------------------------------------------------------
# _episode_metadata_rows — provenance rows + resolved winner
# ---------------------------------------------------------------------------


def test_metadata_rows_shape_covers_all_fields(db_session, episode_factory):
    from audiobiblio.web.views import _episode_metadata_rows

    ep = episode_factory()
    rows = _episode_metadata_rows(db_session, ep)

    assert [r["field"] for r in rows] == [
        "title", "author", "narrator", "genre", "description", "year",
    ]
    for r in rows:
        assert set(r) >= {"field", "current", "resolved_value", "resolved_origin", "history"}


def test_metadata_rows_manual_beats_scraped(db_session, episode_factory):
    """MANUAL + SCRAPED pair for the same field → winner is the manual row."""
    from audiobiblio.core.provenance import record_value
    from audiobiblio.web.views import _episode_metadata_rows

    ep = episode_factory()
    record_value(
        db_session, "episode", ep.id, "title", "Scraped Title",
        FieldOrigin.SCRAPED, "rozhlas",
    )
    record_value(
        db_session, "episode", ep.id, "title", "Manual Title",
        FieldOrigin.MANUAL, "user",
    )
    db_session.flush()  # session has autoflush=False
    # Make the scraped observation newer — MANUAL must still win by rank
    scraped = db_session.query(MetadataValue).filter_by(
        entity_type="episode", entity_id=ep.id, field="title",
        origin=FieldOrigin.SCRAPED,
    ).one()
    scraped.observed_at = datetime.utcnow() + timedelta(hours=1)
    db_session.commit()

    rows = _episode_metadata_rows(db_session, ep)
    title_row = next(r for r in rows if r["field"] == "title")

    assert title_row["resolved_value"] == "Manual Title"
    assert title_row["resolved_origin"] == "manual"
    assert len(title_row["history"]) == 2
    origins = {h["origin"] for h in title_row["history"]}
    assert origins == {"manual", "scraped"}


def test_metadata_rows_work_level_routing_for_author(db_session, episode_factory):
    """author provenance lives on the Work entity (same routing as PATCH)."""
    from audiobiblio.core.provenance import record_value
    from audiobiblio.web.views import _episode_metadata_rows

    ep = episode_factory()
    record_value(
        db_session, "work", ep.work_id, "author", "Karel Čapek",
        FieldOrigin.ENRICHED, "databazeknih",
    )
    db_session.commit()

    rows = _episode_metadata_rows(db_session, ep)
    author_row = next(r for r in rows if r["field"] == "author")

    assert author_row["resolved_value"] == "Karel Čapek"
    assert author_row["resolved_origin"] == "enriched"


def test_metadata_rows_current_orm_values(db_session, episode_factory):
    from audiobiblio.web.views import _episode_metadata_rows

    ep = episode_factory()
    ep.summary = "The summary."
    ep.work.author = "An Author"
    ep.work.year = 1984
    db_session.commit()

    rows = {r["field"]: r for r in _episode_metadata_rows(db_session, ep)}

    assert rows["title"]["current"] == ep.title
    assert rows["description"]["current"] == "The summary."
    assert rows["author"]["current"] == "An Author"
    assert rows["year"]["current"] == "1984"
    assert rows["narrator"]["current"] is None
    assert rows["genre"]["current"] is None


# ---------------------------------------------------------------------------
# Detail page + list links (smoke) + route census
# ---------------------------------------------------------------------------


def test_episode_detail_page_renders(views_client, db_session, episode_factory, tmp_path):
    f = tmp_path / "file.m4a"
    f.write_bytes(b"x")
    ep = episode_factory(program_name="Detail Prog")
    _add_audio_asset(db_session, ep, str(f))
    job = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.SUCCESS)
    db_session.add(job)
    db_session.commit()

    r = views_client.get(f"/episodes/{ep.id}")

    assert r.status_code == 200
    assert ep.title in r.text
    assert "Detail Prog" in r.text                              # breadcrumb
    assert f"/api/v1/episodes/{ep.id}/audio" in r.text          # player src
    assert str(f) in r.text                                     # files table path
    assert "on disk" in r.text                                  # exists badge


def test_episode_detail_page_unknown_id_redirects_to_list(views_client):
    r = views_client.get("/episodes/99999", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/episodes"


def test_episodes_list_titles_link_to_detail(views_client, db_session, episode_factory):
    ep = episode_factory()
    db_session.commit()

    r = views_client.get("/episodes")

    assert r.status_code == 200
    assert f'href="/episodes/{ep.id}"' in r.text


def test_job_rows_partial_links_episode_to_detail(views_client, db_session, episode_factory):
    ep = episode_factory()
    job = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO, status=JobStatus.SUCCESS)
    db_session.add(job)
    db_session.commit()

    r = views_client.get("/_partials/job_rows")

    assert r.status_code == 200
    assert f'href="/episodes/{ep.id}"' in r.text


def test_episode_detail_route_registered():
    """Route census: /episodes/{episode_id} appears in the views router."""
    from audiobiblio.web.views import router as views_router
    paths = [getattr(r, "path", None) for r in views_router.routes]
    assert "/episodes/{episode_id}" in paths


def test_audio_route_registered():
    """Route census: audio endpoint appears in the episodes API router."""
    from audiobiblio.web.routers.episodes import router as episodes_router
    paths = [getattr(r, "path", None) for r in episodes_router.routes]
    assert "/api/v1/episodes/{episode_id}/audio" in paths
