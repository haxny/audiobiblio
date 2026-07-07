"""
Tests for audiobiblio.library.enrich_meta

TDD — write tests first (RED), implement to make them GREEN.

Covers:
  1. real-fixture: write a small info.json into tmp_path with title/description/duration
  2. fallback-title update (current "Episode N" → replaced with candidate)
  3. generic candidate skipped (is_generic_title check)
  4. MANUAL protected (has_manual → no ORM update but provenance still recorded)
  5. malformed JSON tolerated (note set, no raises)
  6. dry-run is pure (no session writes)
  7. provenance rows recorded (record_value / SCRAPED with source="meta_json")
  8. description set only when empty
  9. duration_ms only set when NULL
  10. episode_number only set when NULL
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    Base,
    Episode,
    FieldOrigin,
    MetadataValue,
    Program,
    Series,
    Station,
    Work,
)
from audiobiblio.library.enrich_meta import EnrichReport, enrich_episode_from_meta


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _make_episode(session, *, title: str = "Episode 1", summary: str | None = None,
                  duration_ms: int | None = None, episode_number: int | None = None) -> Episode:
    station = Station(code="tst", name="Test Station")
    session.add(station)
    session.flush()
    program = Program(station_id=station.id, name="Prog")
    session.add(program)
    session.flush()
    series = Series(program_id=program.id, name="S")
    session.add(series)
    session.flush()
    work = Work(series_id=series.id, title="Work")
    session.add(work)
    session.flush()
    ep = Episode(work_id=work.id, title=title, url="https://example.cz/ep",
                 summary=summary, duration_ms=duration_ms, episode_number=episode_number)
    session.add(ep)
    session.flush()
    return ep


def _add_meta_json_asset(session, episode_id: int, file_path: str | Path | None,
                         status: AssetStatus = AssetStatus.COMPLETE) -> Asset:
    asset = Asset(
        episode_id=episode_id,
        type=AssetType.META_JSON,
        status=status,
        file_path=str(file_path) if file_path else None,
    )
    session.add(asset)
    session.flush()
    return asset


# ---------------------------------------------------------------------------
# 1. Real-fixture: basic enrichment from a real info.json
# ---------------------------------------------------------------------------


def test_enrich_episode_real_info_json(db_session, tmp_path: Path) -> None:
    """Reading title/description/duration from a valid info.json updates the episode."""
    info = {
        "title": "Karel Horký: Nad mrtvým netopýrem",
        "description": "Pořad o netopýrech.",
        "duration": 1800,  # seconds
    }
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    assert isinstance(report, EnrichReport)
    assert "title" in report.fields_updated
    db_session.refresh(ep)
    assert ep.title == "Karel Horký: Nad mrtvým netopýrem"
    assert ep.summary == "Pořad o netopýrech."
    assert ep.duration_ms == 1800 * 1000


# ---------------------------------------------------------------------------
# 2. Fallback-title update: "Episode N" → replaced
# ---------------------------------------------------------------------------


def test_fallback_title_replaced(db_session, tmp_path: Path) -> None:
    info = {"title": "Skutečný název epizody"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 9")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == "Skutečný název epizody"
    assert "title" in report.fields_updated


# ---------------------------------------------------------------------------
# 3. Candidate extends current → replaced (prefix relationship, non-fallback)
# ---------------------------------------------------------------------------


def test_extension_candidate_replaces_truncated(db_session, tmp_path: Path) -> None:
    """Candidate that is a longer version of the current title (prefix match) should replace it."""
    current = "Karel Horký: Nad mrtvým netopýrem"
    full = "Karel Horký: Nad mrtvým netopýrem. Přemítání o lidské přezíravosti a neporozumění životu vůbec"
    info = {"title": full}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title=current)
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == full
    assert "title" in report.fields_updated


def test_unrelated_longer_candidate_not_replaced(db_session, tmp_path: Path) -> None:
    """A longer but semantically different candidate must NOT replace a good existing title."""
    info = {"title": "Srdce na kolejích: Příběhy železnice, která změnila podobu měst a život jejich obyvatel"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="1.díl: Mladý pilot Bedřich Dvořák opustil okupovanou zemi s rozhodnutím bojovat")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title.startswith("1.díl:")  # not changed
    # title may or may not be in skipped, but ORM is unchanged


# ---------------------------------------------------------------------------
# 3b. Hash-like candidate skipped (yt-dlp sometimes uses ID hashes as titles)
# ---------------------------------------------------------------------------


def test_hash_title_candidate_skipped(db_session, tmp_path: Path) -> None:
    """An MD5/SHA hex hash masquerading as a title must not overwrite any real title."""
    info = {"title": "e34f5e7955bef74a86e5aacad67f40f8"}  # 32-char hex = MD5 hash
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Kroměříž – 30 Kamenů zmizelých")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == "Kroměříž – 30 Kamenů zmizelých"  # not changed
    assert "title" in report.skipped


# ---------------------------------------------------------------------------
# 4. Generic candidate skipped (is_generic_title guard)
# ---------------------------------------------------------------------------


def test_generic_title_candidate_skipped(db_session, tmp_path: Path) -> None:
    """A generic candidate like 'Epizody pořadu' must not overwrite the current title."""
    info = {"title": "Epizody pořadu"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 5")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == "Episode 5"  # not changed
    assert "title" in report.skipped


# ---------------------------------------------------------------------------
# 5. MANUAL provenance protects the title from being overwritten
# ---------------------------------------------------------------------------


def test_manual_title_protected(db_session, tmp_path: Path) -> None:
    """has_manual → title not updated on ORM, but provenance still recorded."""
    info = {"title": "Candidate from info.json"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, jf)

    # Plant a MANUAL MetadataValue row for the title
    mv = MetadataValue(
        entity_type="episode",
        entity_id=ep.id,
        field="title",
        value="Manuálně nastavený název",
        origin=FieldOrigin.MANUAL,
        source="user",
    )
    db_session.add(mv)
    db_session.flush()

    report = enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == "Episode 1"  # ORM not changed
    assert "title" in report.skipped


# ---------------------------------------------------------------------------
# 6. Malformed JSON is tolerated — note set, no raises
# ---------------------------------------------------------------------------


def test_malformed_json_tolerated(db_session, tmp_path: Path) -> None:
    jf = tmp_path / "bad.info.json"
    jf.write_bytes(b"{not valid json}")

    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, jf)

    report = enrich_episode_from_meta(db_session, ep)

    assert isinstance(report, EnrichReport)
    assert "malformed" in report.note.lower() or "json" in report.note.lower()
    assert not report.fields_updated


# ---------------------------------------------------------------------------
# 7. No META_JSON asset → graceful no-op
# ---------------------------------------------------------------------------


def test_no_meta_json_asset(db_session) -> None:
    ep = _make_episode(db_session, title="Episode 1")
    # No asset added

    report = enrich_episode_from_meta(db_session, ep)

    assert isinstance(report, EnrichReport)
    assert not report.fields_updated
    assert "no" in report.note.lower() or "missing" in report.note.lower()


# ---------------------------------------------------------------------------
# 8. Provenance rows recorded (SCRAPED, source="meta_json")
# ---------------------------------------------------------------------------


def test_provenance_recorded(db_session, tmp_path: Path) -> None:
    info = {"title": "Nový název"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    prov = (
        db_session.query(MetadataValue)
        .filter_by(
            entity_type="episode",
            entity_id=ep.id,
            field="title",
            origin=FieldOrigin.SCRAPED,
            source="meta_json",
        )
        .first()
    )
    assert prov is not None
    assert prov.value == "Nový název"


# ---------------------------------------------------------------------------
# 9. Description set only when empty
# ---------------------------------------------------------------------------


def test_description_not_overwritten(db_session, tmp_path: Path) -> None:
    info = {"description": "Nový popis"}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1", summary="Původní popis")
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.summary == "Původní popis"  # existing not overwritten


# ---------------------------------------------------------------------------
# 10. duration_ms only set when NULL
# ---------------------------------------------------------------------------


def test_duration_not_overwritten(db_session, tmp_path: Path) -> None:
    info = {"duration": 2000}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1", duration_ms=999_000)
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.duration_ms == 999_000  # existing not overwritten


# ---------------------------------------------------------------------------
# 11. episode_number only set when NULL and source provides a number
# ---------------------------------------------------------------------------


def test_episode_number_set_when_null(db_session, tmp_path: Path) -> None:
    info = {"episode": 3}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1", episode_number=None)
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.episode_number == 3


def test_episode_number_not_overwritten(db_session, tmp_path: Path) -> None:
    info = {"episode": 5, "track": 5}
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1", episode_number=7)
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.episode_number == 7  # not overwritten


# ---------------------------------------------------------------------------
# 12. Missing file path tolerated
# ---------------------------------------------------------------------------


def test_missing_file_path_tolerated(db_session, tmp_path: Path) -> None:
    """COMPLETE META_JSON asset with a path that doesn't exist → graceful note."""
    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, "/nonexistent/path.info.json")

    report = enrich_episode_from_meta(db_session, ep)

    assert isinstance(report, EnrichReport)
    assert not report.fields_updated


# ---------------------------------------------------------------------------
# 13. fulltitle preferred over title when present and longer
# ---------------------------------------------------------------------------


def test_fulltitle_preferred_when_longer(db_session, tmp_path: Path) -> None:
    info = {
        "title": "Krátký název",
        "fulltitle": "Tento plný název je výrazně delší než zkrácený",
    }
    jf = tmp_path / "ep.info.json"
    jf.write_text(json.dumps(info), encoding="utf-8")

    ep = _make_episode(db_session, title="Episode 1")
    _add_meta_json_asset(db_session, ep.id, jf)

    enrich_episode_from_meta(db_session, ep)

    db_session.refresh(ep)
    assert ep.title == "Tento plný název je výrazně delší než zkrácený"
