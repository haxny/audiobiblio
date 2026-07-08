"""Tests for audiobiblio/sources/databazeknih.py — all parsing tests use fixtures.

Fixture provenance:
  tests/fixtures/dbk/search_valka_s_mloky.html — live GET
    https://www.databazeknih.cz/search?q=V%C3%A1lka+s+mloky&in=books
  tests/fixtures/dbk/book_valka_s_mloky.html — live GET
    https://www.databazeknih.cz/prehled-knihy/valka-s-mloky-160
  Both fetched 2026-07-08 with UA "audiobiblio/0.5 (personal audiobook manager)".
"""
from __future__ import annotations

import os
import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Base,
    Episode,
    FieldOrigin,
    MetadataValue,
    Program,
    Series,
    Station,
    Work,
)
from audiobiblio.sources.databazeknih import (
    DbkBook,
    DbkHit,
    _parse_book_page,
    _parse_search_hits,
    enrich_work_from_dbk,
)

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "dbk"


@pytest.fixture()
def search_html() -> str:
    return (FIXTURE_DIR / "search_valka_s_mloky.html").read_text(encoding="utf-8")


@pytest.fixture()
def book_html() -> str:
    return (FIXTURE_DIR / "book_valka_s_mloky.html").read_text(encoding="utf-8")


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
def work_with_episodes(db_session):
    """Work (Válka s mloky / Karel Čapek) with 2 episodes, no existing metadata."""
    st = Station(code="dbk", name="Test")
    db_session.add(st)
    db_session.flush()
    prog = Program(station_id=st.id, name="TestProg")
    db_session.add(prog)
    db_session.flush()
    ser = Series(program_id=prog.id, name="TestSeries")
    db_session.add(ser)
    db_session.flush()
    w = Work(series_id=ser.id, title="Válka s mloky", author="Karel Čapek")
    db_session.add(w)
    db_session.flush()
    ep1 = Episode(work_id=w.id, title="Válka s mloky 1", ext_id="dbk-ep-1")
    ep2 = Episode(work_id=w.id, title="Válka s mloky 2", ext_id="dbk-ep-2")
    db_session.add_all([ep1, ep2])
    db_session.flush()
    db_session.expire_all()
    return db_session.get(Work, w.id)


# ---------------------------------------------------------------------------
# Search page parsing
# ---------------------------------------------------------------------------


class TestParseSearchHits:
    def test_finds_hits(self, search_html):
        hits = _parse_search_hits(search_html)
        assert len(hits) > 0

    def test_hits_are_dbk_hit_objects(self, search_html):
        hits = _parse_search_hits(search_html)
        assert all(isinstance(h, DbkHit) for h in hits)

    def test_first_capek_hit_present(self, search_html):
        hits = _parse_search_hits(search_html)
        capek = next((h for h in hits if "Karel Čapek" in (h.author or "")), None)
        assert capek is not None, "Expected a Karel Čapek hit in search results"

    def test_hit_url_is_absolute_prehled_knihy(self, search_html):
        hits = _parse_search_hits(search_html)
        for h in hits:
            assert h.url.startswith("https://www.databazeknih.cz/prehled-knihy/")

    def test_hit_title_non_empty(self, search_html):
        hits = _parse_search_hits(search_html)
        for h in hits:
            assert h.title

    def test_returns_empty_list_on_empty_html(self):
        hits = _parse_search_hits("<html><body></body></html>")
        assert hits == []

    def test_returns_empty_list_on_parser_error(self, monkeypatch):
        """BeautifulSoup parse error is caught; never raises (honors never-raise contract)."""
        # Monkeypatch BeautifulSoup to raise an exception during construction
        import audiobiblio.sources.databazeknih as dbk_module

        original_soup = dbk_module.BeautifulSoup

        def mock_soup(*args, **kwargs):
            raise RuntimeError("Simulated BeautifulSoup parsing failure")

        monkeypatch.setattr(dbk_module, "BeautifulSoup", mock_soup)

        # Should return [] without raising
        hits = _parse_search_hits("<html><body></body></html>")
        assert hits == []
        assert isinstance(hits, list)


# ---------------------------------------------------------------------------
# Book page parsing
# ---------------------------------------------------------------------------


class TestParseBookPage:
    def test_returns_dbk_book(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert isinstance(book, DbkBook)

    def test_title_contains_valka(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert "Válka" in book.title

    def test_author_is_capek(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert book.author == "Karel Čapek"

    def test_year_is_int_in_range(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert isinstance(book.year, int)
        assert 1900 <= book.year <= 2030

    def test_genres_non_empty(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert len(book.genres) > 0

    def test_genres_include_known_values(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        known = {"Romány", "Sci-fi", "Literatura česká"}
        assert known & set(book.genres), f"Expected at least one of {known}, got {book.genres}"

    def test_description_non_empty(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert book.description is not None
        assert len(book.description) > 50

    def test_cover_url_is_https(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert book.cover_url is not None
        assert book.cover_url.startswith("https://")

    def test_narrator_is_none_for_standard_book(self, book_html):
        book = _parse_book_page(book_html)
        assert book is not None
        assert book.narrator is None

    def test_returns_none_on_empty_html(self):
        result = _parse_book_page("<html><body></body></html>")
        assert result is None


# ---------------------------------------------------------------------------
# enrich_work_from_dbk — guards, routing, and cache
# ---------------------------------------------------------------------------


class TestEnrichWorkFromDbk:
    def _make_hit(self) -> DbkHit:
        return DbkHit(
            url="https://www.databazeknih.cz/prehled-knihy/valka-s-mloky-160",
            title="Válka s Mloky",
            author="Karel Čapek",
        )

    def _make_book(self, **overrides) -> DbkBook:
        defaults = dict(
            title="Válka s Mloky",
            author="Karel Čapek",
            year=1936,
            description="A compelling satirical novel about newts taking over the world.",
            genres=["Sci-fi", "Romány"],
            narrator=None,
            cover_url="https://example.com/cover.jpg",
        )
        defaults.update(overrides)
        return DbkBook(**defaults)

    def test_ambiguous_skip_when_no_match(self, db_session, monkeypatch):
        """Work with nonsense title → search returns nothing → skipped."""
        st = Station(code="sk2", name="Skip Test")
        db_session.add(st)
        db_session.flush()
        prog = Program(station_id=st.id, name="SkipProg")
        db_session.add(prog)
        db_session.flush()
        ser = Series(program_id=prog.id, name="SkipSeries")
        db_session.add(ser)
        db_session.flush()
        work = Work(series_id=ser.id, title="xyzzy12345nonexistent", author=None)
        db_session.add(work)
        db_session.flush()

        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [],
        )

        report = enrich_work_from_dbk(db_session, work)
        assert report.skipped is True
        assert report.reason is not None

    def test_ambiguous_skip_when_score_too_low(self, db_session, monkeypatch):
        """Hits with poor title match → score < 0.85 → skipped 'ambiguous'."""
        st = Station(code="sk3", name="Ambig Test")
        db_session.add(st)
        db_session.flush()
        prog = Program(station_id=st.id, name="AmbigProg")
        db_session.add(prog)
        db_session.flush()
        ser = Series(program_id=prog.id, name="AmbigSeries")
        db_session.add(ser)
        db_session.flush()
        work = Work(series_id=ser.id, title="Zelená hora", author="Neznámý autor")
        db_session.add(work)
        db_session.flush()

        # Return a completely unrelated book — score will be well below 0.85
        bad_hit = DbkHit(
            url="https://www.databazeknih.cz/prehled-knihy/neco-jineho-99999",
            title="Noco fabulant přechod",
            author="Jiný Autor",
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [bad_hit],
        )

        report = enrich_work_from_dbk(db_session, work)
        assert report.skipped is True
        assert "ambiguous" in (report.reason or "").lower()

    def test_sets_year_when_work_year_is_none(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """When work.year is None and hit matches well, ORM year is set."""
        hit = self._make_hit()
        book = self._make_book(year=1936)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        assert work.year is None
        report = enrich_work_from_dbk(db_session, work)

        assert report.skipped is False
        assert "year" in report.fields_set
        db_session.refresh(work)
        assert work.year == 1936

    def test_does_not_overwrite_existing_year(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """When work.year is already set, ORM column is not overwritten."""
        hit = self._make_hit()
        book = self._make_book(year=2007)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        work.year = 1936  # pre-existing value
        db_session.flush()

        enrich_work_from_dbk(db_session, work)
        db_session.refresh(work)
        assert work.year == 1936  # unchanged

    def test_guards_manual_provenance_on_year(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """When MANUAL provenance exists for year, ORM column not set."""
        from audiobiblio.core.provenance import record_value as rv

        hit = self._make_hit()
        book = self._make_book(year=2007)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        # Record MANUAL year without setting ORM (testing the guard)
        rv(db_session, "work", work.id, "year", "1936", FieldOrigin.MANUAL, "user")
        db_session.flush()

        enrich_work_from_dbk(db_session, work)
        db_session.refresh(work)
        # ORM stays None because MANUAL guard blocks the update
        assert work.year is None

    def test_year_enriched_provenance_row_recorded(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """ENRICHED provenance row for year is always recorded (even if ORM not updated)."""
        hit = self._make_hit()
        book = self._make_book(year=1936)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        enrich_work_from_dbk(db_session, work)

        mv = (
            db_session.query(MetadataValue)
            .filter_by(
                entity_type="work",
                entity_id=work.id,
                field="year",
                origin=FieldOrigin.ENRICHED,
                source="databazeknih",
            )
            .first()
        )
        assert mv is not None
        assert mv.value == "1936"

    def test_description_provenance_on_work(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Description is recorded as ENRICHED provenance on work entity (no ORM column)."""
        hit = self._make_hit()
        book = self._make_book(description="Satirický román o mlocích.")
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        report = enrich_work_from_dbk(db_session, work)

        assert "description" in report.fields_set
        mv = (
            db_session.query(MetadataValue)
            .filter_by(entity_type="work", entity_id=work.id, field="description")
            .first()
        )
        assert mv is not None
        assert mv.origin == FieldOrigin.ENRICHED
        assert "mlocích" in mv.value

    def test_genre_recorded_for_each_episode(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Genre routes to episode-level: ENRICHED genre row per episode."""
        hit = self._make_hit()
        book = self._make_book(genres=["Sci-fi", "Romány"])
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        episode_ids = [ep.id for ep in work.episodes]
        report = enrich_work_from_dbk(db_session, work)

        assert "genre" in report.fields_set
        for ep_id in episode_ids:
            mv = (
                db_session.query(MetadataValue)
                .filter_by(
                    entity_type="episode", entity_id=ep_id, field="genre",
                    origin=FieldOrigin.ENRICHED,
                )
                .first()
            )
            assert mv is not None, f"No genre row for episode {ep_id}"
            assert "Sci-fi" in mv.value

    def test_narrator_recorded_per_episode_when_present(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Narrator routes to episode-level: recorded on each episode when non-None."""
        hit = self._make_hit()
        book = self._make_book(narrator="Jan Vlasák")
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        episode_ids = [ep.id for ep in work.episodes]
        report = enrich_work_from_dbk(db_session, work)

        assert "narrator" in report.fields_set
        for ep_id in episode_ids:
            mv = (
                db_session.query(MetadataValue)
                .filter_by(
                    entity_type="episode", entity_id=ep_id, field="narrator",
                    origin=FieldOrigin.ENRICHED,
                )
                .first()
            )
            assert mv is not None, f"No narrator row for episode {ep_id}"
            assert mv.value == "Jan Vlasák"

    def test_narrator_not_recorded_when_none(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Narrator is not recorded when book.narrator is None."""
        hit = self._make_hit()
        book = self._make_book(narrator=None)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        report = enrich_work_from_dbk(db_session, work)

        assert "narrator" not in report.fields_set
        count = (
            db_session.query(MetadataValue)
            .filter_by(entity_type="episode", field="narrator")
            .count()
        )
        assert count == 0

    def test_cache_write_sets_dbk_key(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Raw book result is stored in work.extra['dbk'] via dict reassignment."""
        hit = self._make_hit()
        book = self._make_book(year=2007)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        enrich_work_from_dbk(db_session, work)

        db_session.refresh(work)
        assert work.extra is not None
        assert "dbk" in work.extra
        assert work.extra["dbk"]["title"] == "Válka s Mloky"
        assert work.extra["dbk"]["year"] == 2007
        assert work.extra["dbk"]["url"] == hit.url

    def test_cache_preserves_existing_extra_keys(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Dict reassignment preserves pre-existing keys in work.extra."""
        hit = self._make_hit()
        book = self._make_book()
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: book,
        )

        work = work_with_episodes
        work.extra = {"existing_key": "existing_value"}
        db_session.flush()

        enrich_work_from_dbk(db_session, work)

        db_session.refresh(work)
        assert work.extra.get("existing_key") == "existing_value"
        assert "dbk" in work.extra

    def test_fetch_failed_skip_after_successful_fuzzy_match(
        self, db_session, work_with_episodes, monkeypatch
    ):
        """Search succeeds and matches well, but fetch returns None → skipped with reason 'fetch failed'."""
        hit = self._make_hit()
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.search_book",
            lambda title, author=None: [hit],
        )
        # fetch_book returns None (network error, parse failure, etc.)
        monkeypatch.setattr(
            "audiobiblio.sources.databazeknih.fetch_book",
            lambda url: None,
        )

        work = work_with_episodes
        report = enrich_work_from_dbk(db_session, work)

        assert report.skipped is True
        assert report.reason == "fetch failed"
        assert len(report.fields_set) == 0


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE"),
    reason="Skipped unless RUN_LIVE=1 env var is set",
)
class TestLiveFetch:
    """Integration tests against the real databazeknih.cz — skipped in CI."""

    def test_search_live_returns_hits(self):
        from audiobiblio.sources.databazeknih import search_book

        hits = search_book("Válka s mloky", "Karel Čapek")
        assert len(hits) > 0

    def test_fetch_book_live_returns_dbk_book(self):
        from audiobiblio.sources.databazeknih import fetch_book

        book = fetch_book(
            "https://www.databazeknih.cz/prehled-knihy/valka-s-mloky-160"
        )
        assert book is not None
        assert book.author == "Karel Čapek"
