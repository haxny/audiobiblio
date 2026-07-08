"""Tests for POST /api/v1/ingest/url/preview endpoint.

Uses monkeypatched probe_url with two canned responses keyed by URL to avoid
any network calls.
"""
from __future__ import annotations
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audiobiblio.core.db.models import Base
from audiobiblio.web.deps import get_db

EPISODE_URL = "https://www.mujrozhlas.cz/hajaja/nazev-epizody"
PROGRAM_URL = "https://www.mujrozhlas.cz/hajaja"
SERIES_URL = "https://www.mujrozhlas.cz/cetba-na-pokracovani/Jakob-Wassermann"
SERIES_PROGRAM_URL = "https://www.mujrozhlas.cz/cetba-na-pokracovani"

CANNED_EPISODE_PROBE = {
    "extractor_key": "MujRozhlasIE",
    "title": "Název epizody",
    "webpage_url": EPISODE_URL,
    "uploader": "Radiožurnál",
}

CANNED_PROGRAM_PROBE = {
    "extractor_key": "MujRozhlasIE",
    "title": "Hajaja",
    "webpage_url": PROGRAM_URL,
    "entries": [
        {"title": f"Ep {i}", "webpage_url": f"{PROGRAM_URL}/ep-{i}"}
        for i in range(5)
    ],
}

CANNED_SERIES_PROBE = {
    "extractor_key": "MujRozhlasIE",
    "title": "Jakob Wassermann",
    "series": "Čtba na pokračování",
    "webpage_url": SERIES_URL,
    "entries": [
        {"title": f"Díl {i}", "webpage_url": f"{SERIES_URL}/dil-{i}"}
        for i in range(12)
    ],
}

CANNED_SERIES_PROGRAM_PROBE = {
    "extractor_key": "MujRozhlasIE",
    "title": "Čtba na pokračování",
    "webpage_url": SERIES_PROGRAM_URL,
    "entries": [
        {"title": f"Čtba {i}", "webpage_url": f"{SERIES_PROGRAM_URL}/ctba-{i}"}
        for i in range(8)
    ],
}

CANNED_PROBES = {
    EPISODE_URL: CANNED_EPISODE_PROBE,
    PROGRAM_URL: CANNED_PROGRAM_PROBE,
    SERIES_URL: CANNED_SERIES_PROBE,
    SERIES_PROGRAM_URL: CANNED_SERIES_PROGRAM_PROBE,
}


@pytest.fixture()
def _patch_probe(monkeypatch):
    """Patch probe_url at the source so all callers see canned responses."""
    monkeypatch.setattr(
        "audiobiblio.sources.mrz_inspector.probe_url",
        lambda url: CANNED_PROBES.get(url, CANNED_PROGRAM_PROBE),
    )


@pytest.fixture()
def db_ingest():
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
def ingest_client(db_ingest, _patch_probe):
    from audiobiblio.web.routers import ingest as ingest_router

    app = FastAPI()
    app.include_router(ingest_router.router)

    def _override_db():
        yield db_ingest

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


class TestUrlPreviewEndpoint:
    """POST /api/v1/ingest/url/preview responds correctly."""

    def test_episode_url_returns_parent_block(self, ingest_client):
        r = ingest_client.post(
            "/api/v1/ingest/url/preview",
            json={"url": EPISODE_URL},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["kind"] == "episode"
        assert data["parent"] is not None
        assert data["parent"]["url"] == PROGRAM_URL
        assert data["parent"]["title"] == "Hajaja"
        assert data["parent"]["episode_count"] == 5

    def test_episode_url_response_has_base_shape(self, ingest_client):
        """Existing response fields must still be present (additive contract)."""
        r = ingest_client.post(
            "/api/v1/ingest/url/preview",
            json={"url": EPISODE_URL},
        )
        data = r.json()
        for field in ("raw_count", "unique_count", "reairs", "already_in_db", "episodes"):
            assert field in data, f"missing field: {field}"
        assert data["raw_count"] >= 1

    def test_program_url_returns_null_parent(self, ingest_client):
        """A program-level (depth-1) URL should return parent: null."""
        r = ingest_client.post(
            "/api/v1/ingest/url/preview",
            json={"url": PROGRAM_URL},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["parent"] is None

    def test_preview_parent_probe_failure_degrades(self, monkeypatch, ingest_client):
        """When parent URL probe fails, return degraded parent block (title=None, episode_count=0)."""
        # Monkeypatch probe_url to raise for PARENT url only
        original_canned = dict(CANNED_PROBES)

        def patched_probe(url):
            if url == PROGRAM_URL:
                raise RuntimeError("Network error probing parent")
            return original_canned.get(url, CANNED_PROGRAM_PROBE)

        monkeypatch.setattr(
            "audiobiblio.sources.mrz_inspector.probe_url",
            patched_probe,
        )

        r = ingest_client.post(
            "/api/v1/ingest/url/preview",
            json={"url": EPISODE_URL},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["kind"] == "episode"
        assert data["parent"] is not None
        assert data["parent"]["url"] == PROGRAM_URL
        assert data["parent"]["title"] is None  # Degraded: title unavailable
        assert data["parent"]["episode_count"] == 0  # Degraded: count unavailable

    def test_series_url_returns_parent_block(self, ingest_client):
        """A series-kind URL (multi-part reading) should also return parent block."""
        r = ingest_client.post(
            "/api/v1/ingest/url/preview",
            json={"url": SERIES_URL},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["kind"] == "series"
        assert data["parent"] is not None
        assert data["parent"]["url"] == SERIES_PROGRAM_URL
        assert data["parent"]["title"] == "Čtba na pokračování"
        assert data["parent"]["episode_count"] == 8
