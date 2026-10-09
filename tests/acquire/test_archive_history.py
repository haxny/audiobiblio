"""Weekly archive walk for REVIEW targets: aired history, no probes, no jobs."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from audiobiblio.acquire import archive_history as ah
from audiobiblio.core.db.models import (
    ApprovalMode, AvailabilityStatus, CrawlTarget, CrawlTargetKind, DownloadJob,
    Episode, EpisodeAlias, Program, Series, Station, Work,
)
from audiobiblio.sources.rozhlas_station import ArticleStub

URL = "https://dvojka.rozhlas.cz/desky-pasky-vzpominky-7800955"


def _target(db, url=URL):
    t = CrawlTarget(url=url, kind=CrawlTargetKind.PROGRAM, name="Desky, pásky, vzpomínky",
                    active=True, interval_hours=24, approval_mode=ApprovalMode.REVIEW)
    db.add(t)
    db.flush()
    return t


def _ep(db, title, published_at, url="https://portal.rozhlas.cz/x.mp3", ext="111",
        program="Desky, pásky, vzpomínky", status=AvailabilityStatus.AVAILABLE):
    st = db.query(Station).filter_by(code="CRo2").first()
    if st is None:
        st = Station(code="CRo2", name="Dvojka"); db.add(st); db.flush()
    p = Program(station_id=st.id, name=program); db.add(p); db.flush()
    se = Series(program_id=p.id, name=p.name); db.add(se); db.flush()
    w = Work(series_id=se.id, title=p.name); db.add(w); db.flush()
    ep = Episode(work_id=w.id, title=title, url=url, ext_id=ext, published_at=published_at,
                 availability_status=status)
    db.add(ep); db.flush()
    return ep


def _walk(db, target, stubs):
    with patch.object(ah, "fetch_archive_stubs",
                      side_effect=lambda url, is_known=None, **kw: [
                          s for s in stubs if not (is_known and is_known(s))]):
        return ah.walk_archive_history(db, target, URL)


def test_unknown_card_becomes_gone_stub_without_jobs(db_session):
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/stara-deska-1234567",
                     title="Stará deska", published_at=datetime(2019, 3, 1), perex="Anotace")
    stats = _walk(db_session, t, [st])
    assert stats["gone"] == 1
    ep = db_session.query(Episode).filter_by(url=st.url).one()
    assert ep.availability_status == AvailabilityStatus.GONE
    assert ep.published_at == datetime(2019, 3, 1) and ep.summary == "Anotace"
    assert db_session.query(DownloadJob).count() == 0


def test_card_of_rapi_indexed_broadcast_becomes_alias(db_session):
    ep = _ep(db_session, "Jiří Suchý vzpomíná", datetime(2026, 10, 4, 18, 0))
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/jiri-suchy-vzpomina-9650000",
                     title="Jiří Suchý vzpomíná", published_at=datetime(2026, 10, 4),
                     perex="Perex")
    stats = _walk(db_session, t, [st])
    assert stats["aliased"] == 1 and stats["gone"] == 0
    assert db_session.query(Episode).count() == 1
    alias = db_session.query(EpisodeAlias).filter_by(episode_id=ep.id).one()
    assert "jiri-suchy-vzpomina" in alias.url
    assert ep.summary == "Perex"


def test_second_walk_is_idle(db_session):
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/a-1234567", title="A",
                     published_at=datetime(2019, 3, 1), perex="p")
    _walk(db_session, t, [st])
    stats = _walk(db_session, t, [st])
    assert stats["stubs"] == 0


def test_twin_in_another_program_is_not_used(db_session):
    _ep(db_session, "Zprávy", datetime(2019, 3, 1, 8), program="Jiný pořad")
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/zpravy-1234567", title="Zprávy",
                     published_at=datetime(2019, 3, 1), perex=None)
    stats = _walk(db_session, t, [st])
    assert stats["aliased"] == 0 and stats["gone"] == 1


def test_near_title_same_day_is_the_same_broadcast(db_session):
    ep = _ep(db_session, "Jiri Sucky vzpomina na Semafor", datetime(2019, 3, 1, 18))
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/x-1234567",
                     title="Jiří Suchý vzpomíná na Semafor", published_at=datetime(2019, 3, 1),
                     perex=None)
    stats = _walk(db_session, t, [st])
    assert stats["aliased"] == 1 and db_session.query(Episode).count() == 1
    assert ep.id


def test_live_episode_that_day_blocks_a_stub(db_session):
    """Card title differs from the rAPI part title: never a GONE duplicate."""
    _ep(db_session, "1. díl", datetime(2019, 3, 1, 18))
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/kniha-1234567", title="Autor: Kniha",
                     published_at=datetime(2019, 3, 1), perex=None)
    stats = _walk(db_session, t, [st])
    assert stats["skipped_live"] == 1 and stats["gone"] == 0


def test_recent_card_is_left_to_rapi(db_session):
    from audiobiblio.core.time import utcnow
    t = _target(db_session)
    st = ArticleStub(url="https://dvojka.rozhlas.cz/novy-1234567", title="Nový",
                     published_at=utcnow(), perex=None)
    stats = _walk(db_session, t, [st])
    assert stats["skipped_recent"] == 1 and db_session.query(Episode).count() == 0


def test_failed_walk_is_recorded_incomplete_and_retried_in_full(db_session, tmp_path, monkeypatch):
    from audiobiblio.acquire import rapi_backfill_state as rbs
    monkeypatch.setattr(rbs, "_path", lambda filename=None: tmp_path / "aw.json")
    t = _target(db_session)
    t.id = 3
    calls = []

    def failing(url, is_known=None, **kw):
        calls.append(is_known is not None)
        raise RuntimeError("page 2 timeout")

    monday = datetime(2026, 10, 8)  # weekday 3 == 3 % 7 → first walk due
    with patch.object(ah, "utcnow", return_value=monday), \
         patch.object(ah, "fetch_archive_stubs", side_effect=failing):
        assert ah.maybe_walk_archive(db_session, t) is None
        ah.maybe_walk_archive(db_session, t)  # retried right away (incomplete)
    assert calls == [True, False]  # second attempt: full walk, no early stop


class TestScheduling:
    def test_first_walk_spread_by_id(self):
        t = CrawlTarget(id=10, url=URL)
        monday = datetime(2026, 10, 5)  # weekday 0; 10 % 7 == 3 → Thursday
        assert not ah.is_due(t, {}, monday)
        assert ah.is_due(t, {}, datetime(2026, 10, 8))

    def test_weekly_after_last_walk(self):
        t = CrawlTarget(id=10, url=URL)
        state = {"10": "2026-10-01T00:00:00"}
        assert not ah.is_due(t, state, datetime(2026, 10, 7))
        assert ah.is_due(t, state, datetime(2026, 10, 8))

    def test_no_station_page_no_walk(self, db_session):
        t = _target(db_session, url="https://www.mujrozhlas.cz/desky")
        assert ah.maybe_walk_archive(db_session, t) is None
