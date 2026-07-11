"""Pasting a multi-part book URL must ingest ALL parts, not entries[0].

mujrozhlas book pages classify as kind="series" with one entry per part
(same page URL, distinct ext_ids). The /api/v1/ingest/url flow previously
rejected them ("Not a single episode URL") or took only the first entry.
"""
from __future__ import annotations

from audiobiblio.core.db.models import Episode, Work
from audiobiblio.sources.mrz_inspector import EpisodeItem, ProbeResult
from audiobiblio.web.routers.ingest import ingest_all_entries

BOOK_URL = (
    "https://www.mujrozhlas.cz/cetba-s-hvezdickou/"
    "margaret-attwoodova-pribeh-sluzebnice"
)


def _book_probe(n: int) -> ProbeResult:
    return ProbeResult(
        kind="series", url=BOOK_URL,
        title="Margaret Atwoodová: Příběh služebnice",
        series="Četba s hvězdičkou", uploader="mujrozhlas",
        extractor="mujrozhlas",
        entries=[
            EpisodeItem(
                url=BOOK_URL,
                title="Margaret Atwoodová: Příběh služebnice",
                episode_number=i,
                series="Četba s hvězdičkou",
                uploader="mujrozhlas",
                ext_id=str(12087680 + i),
                duration_s=1500.0,
            )
            for i in range(1, n + 1)
        ],
    )


def test_ingest_all_entries_creates_episode_per_part(db_session):
    msg = ingest_all_entries(db_session, _book_probe(12))

    eps = db_session.query(Episode).all()
    assert len(eps) == 12
    assert sorted(e.episode_number for e in eps) == list(range(1, 13))
    assert len({e.ext_id for e in eps}) == 12
    assert "12 episode" in msg


def test_ingest_all_entries_single_work_for_the_book(db_session):
    ingest_all_entries(db_session, _book_probe(12))

    works = db_session.query(Work).all()
    assert len(works) == 1
    assert works[0].title == "Margaret Atwoodová: Příběh služebnice"


def test_ingest_all_entries_idempotent(db_session):
    ingest_all_entries(db_session, _book_probe(12))
    ingest_all_entries(db_session, _book_probe(12))

    assert db_session.query(Episode).count() == 12


def test_ingest_all_entries_empty_probe(db_session):
    pr = ProbeResult(kind="series", url=BOOK_URL, title="X",
                     series=None, uploader=None, extractor=None)
    assert "No episodes" in ingest_all_entries(db_session, pr)


def test_ingest_all_entries_rejects_program_urls(db_session):
    pr = _book_probe(2)
    pr.kind = "program"
    msg = ingest_all_entries(db_session, pr)
    assert db_session.query(Episode).count() == 0
    assert "program" in msg.lower()
