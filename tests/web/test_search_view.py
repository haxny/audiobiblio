"""Tests for the /search global search view.

Covers the pure-ish helper _query_search (same pattern as
tests/web/test_gaps_view.py) plus the GET /search route mounted on a
minimal views-router app (same pattern as test_episode_detail.py).

TDD: written before implementation (RED phase).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.core.db.models import Episode, Program, Series, Station, Work
from audiobiblio.web.deps import get_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _setup(db, code: str) -> Series:
    """Station → Program → Series chain, deduplicated by station code."""
    st = db.query(Station).filter_by(code=code).first()
    if not st:
        st = Station(code=code, name=f"ST-{code}")
        db.add(st)
        db.flush()
    p = Program(station_id=st.id, name=f"P-{code}")
    db.add(p)
    db.flush()
    s = Series(program_id=p.id, name=f"S-{code}")
    db.add(s)
    db.flush()
    return s


_n = {"v": 0}


def _work(db, series, *, title: str, author: str | None = None) -> Work:
    w = Work(series_id=series.id, title=title, author=author)
    db.add(w)
    db.flush()
    return w


def _ep(db, work, *, title: str | None = None, summary: str | None = None) -> Episode:
    _n["v"] += 1
    ep = Episode(
        work_id=work.id,
        title=title or f"Sv{_n['v']}",
        summary=summary,
        url=f"https://example.cz/sv-{_n['v']}",
    )
    db.add(ep)
    db.flush()
    return ep


# ---------------------------------------------------------------------------
# _query_search
# ---------------------------------------------------------------------------

class TestQuerySearch:
    def test_empty_query_returns_empty_sections(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv0")
        work = _work(db_session, series, title="Anything")
        _ep(db_session, work, title="Anything ep")

        res = _query_search(db_session, "")
        assert res["works"] == []
        assert res["episodes"] == []
        assert res["programs"] == []
        assert res["works_total"] == 0
        assert res["episodes_total"] == 0
        assert res["programs_total"] == 0

    def test_work_title_hit(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv1")
        work = _work(db_session, series, title="Osudy dobrého vojáka Švejka")
        _work(db_session, series, title="Something else")

        res = _query_search(db_session, "vojáka")
        assert res["works_total"] == 1
        assert res["works"][0]["work_id"] == work.id
        assert res["works"][0]["title"] == "Osudy dobrého vojáka Švejka"

    def test_work_author_hit(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv2")
        work = _work(db_session, series, title="Povídky", author="Karel Čapek")

        res = _query_search(db_session, "Čapek")
        assert res["works_total"] == 1
        assert res["works"][0]["work_id"] == work.id
        assert res["works"][0]["author"] == "Karel Čapek"

    def test_work_row_has_first_episode_id(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv3")
        work = _work(db_session, series, title="FirstEpWork")
        ep1 = _ep(db_session, work, title="part one")
        _ep(db_session, work, title="part two")

        res = _query_search(db_session, "FirstEpWork")
        assert res["works"][0]["first_episode_id"] == ep1.id

    def test_episode_title_hit(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv4")
        work = _work(db_session, series, title="EpTitleWork")
        ep = _ep(db_session, work, title="Tajemný hrad v Karpatech")

        res = _query_search(db_session, "Karpatech")
        assert res["episodes_total"] == 1
        assert res["episodes"][0]["episode_id"] == ep.id
        assert res["episodes"][0]["title"] == "Tajemný hrad v Karpatech"

    def test_episode_summary_hit(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv5")
        work = _work(db_session, series, title="EpSummaryWork")
        ep = _ep(db_session, work, title="Plain title",
                 summary="Příběh o detektivovi z Ostravy")

        res = _query_search(db_session, "detektivovi")
        assert res["episodes_total"] == 1
        assert res["episodes"][0]["episode_id"] == ep.id

    def test_program_name_hit(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv6")

        res = _query_search(db_session, "P-sv6")
        assert res["programs_total"] == 1
        assert res["programs"][0]["name"] == "P-sv6"
        assert res["programs"][0]["program_id"] == series.program_id

    def test_diacritics_insensitive_hasek(self, db_session):
        """Search 'hasek' must find author 'Hašek' (stripped-variant match)."""
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv7")
        work = _work(db_session, series, title="Švejk", author="Jaroslav Hašek")

        res = _query_search(db_session, "hasek")
        assert res["works_total"] == 1
        assert res["works"][0]["work_id"] == work.id

    def test_diacritics_query_finds_stripped_value(self, db_session):
        """The reverse direction: query with diacritics, DB without them."""
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv8")
        work = _work(db_session, series, title="Zert", author="Milan Kundera")

        res = _query_search(db_session, "žert")
        assert res["works_total"] == 1
        assert res["works"][0]["work_id"] == work.id

    def test_case_insensitive(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv9")
        _work(db_session, series, title="VELKÝ TITUL")

        res = _query_search(db_session, "velký titul")
        assert res["works_total"] == 1

    def test_sections_capped_at_50_with_uncapped_total(self, db_session):
        from audiobiblio.web.views import _query_search

        series = _setup(db_session, "sv10")
        work = _work(db_session, series, title="CapWork")
        for i in range(60):
            _ep(db_session, work, title=f"capep {i:03d}")

        res = _query_search(db_session, "capep")
        assert len(res["episodes"]) == 50
        assert res["episodes_total"] == 60


# ---------------------------------------------------------------------------
# GET /search route
# ---------------------------------------------------------------------------

@pytest.fixture()
def views_client(db_session):
    """Test app with only the views (HTML) router mounted.

    Safe here: the /search route doesn't touch load_config() or the scheduler.
    """
    from audiobiblio.web.views import router as views_router

    app = FastAPI()
    app.include_router(views_router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


class TestSearchRoute:
    def test_renders_results(self, db_session, views_client):
        series = _setup(db_session, "sr1")
        _work(db_session, series, title="RouteHitWork", author="Jaroslav Hašek")

        resp = views_client.get("/search", params={"q": "hasek"})
        assert resp.status_code == 200
        assert "RouteHitWork" in resp.text

    def test_empty_q_shows_empty_state(self, views_client):
        resp = views_client.get("/search")
        assert resp.status_code == 200
        # no crash, renders the empty-state page
        assert "search" in resp.text.lower() or "Hledat" in resp.text
