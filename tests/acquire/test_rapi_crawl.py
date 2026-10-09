"""Tests for the rAPI-first crawl of REVIEW targets.

Network is stubbed (resolve_show_uuid / fetch_recent_episodes); DB uses the
in-memory fixture.
"""
from __future__ import annotations

from unittest.mock import patch

from audiobiblio.acquire import rapi_crawl
from audiobiblio.core.db.models import (
    ApprovalMode, AvailabilityStatus, CrawlTarget, CrawlTargetKind, DownloadJob, Episode,
    Program, Series, Station, Work,
)

SHOW = "01883023-cf70-3469-ba1b-0f4a3ba5a224"


def _target(db, url="https://www.mujrozhlas.cz/vyvar", name="VýVar", paired=None,
            mode=ApprovalMode.REVIEW):
    t = CrawlTarget(url=url, kind=CrawlTargetKind.PROGRAM, name=name, active=True,
                    interval_hours=24, approval_mode=mode, paired_url=paired)
    db.add(t)
    db.flush()
    return t


def _existing(db, url, ext_id=None, title="Léto", program="VýVar"):
    st = db.query(Station).filter_by(code="cro1").first()
    if not st:
        st = Station(code="cro1", name="Radiožurnál")
        db.add(st)
        db.flush()
    p = db.query(Program).filter_by(name=program).first()
    if not p:
        p = Program(station_id=st.id, name=program)
        db.add(p)
        db.flush()
        db.add(Series(program_id=p.id, name=program))
        db.flush()
    se = db.query(Series).filter_by(program_id=p.id).first()
    w = db.query(Work).filter_by(series_id=se.id).first()
    if not w:
        w = Work(series_id=se.id, title=program)
        db.add(w)
        db.flush()
    ep = Episode(work_id=w.id, title=title, url=url, ext_id=ext_id)
    db.add(ep)
    db.flush()
    return ep


def _rapi_ep(uuid, cid, title="Nový díl", serial=None, part=None, live=True):
    attrs = {"title": title, "since": "2026-10-07T13:00:00+02:00", "part": part,
             "description": "<p>Popis</p>",
             "audioLinks": [{"linkType": "download", "variant": "mp3", "duration": 495,
                             "url": f"https://portal.rozhlas.cz/{cid}.mp3"}] if live else []}
    if serial:
        attrs["mirroredSerial"] = {"title": serial, "totalParts": 3}
    return {"id": uuid, "meta": {"ga": {"contentId": cid}}, "attributes": attrs}


def _run(db, target, episodes, uuid=SHOW, state=None):
    """Stubbed crawl; `state` is the in-memory backfill cursor store."""
    from audiobiblio.sources.rapi import EpisodeWalk
    store = {} if state is None else state

    def fake_fetch(u, needs_action, **kw):
        return EpisodeWalk([e for e in episodes if needs_action(e)], len(episodes), True)

    with patch.object(rapi_crawl, "resolve_show_uuid", return_value=uuid), \
         patch.object(rapi_crawl, "fetch_recent_episodes", side_effect=fake_fetch), \
         patch.object(rapi_crawl.rapi_backfill_state, "load", side_effect=lambda: dict(store)), \
         patch.object(rapi_crawl.rapi_backfill_state, "save", side_effect=store.update):
        return rapi_crawl.crawl_target_via_rapi(db, target)


def test_unresolvable_show_returns_none(db_session):
    t = _target(db_session)
    assert _run(db_session, t, [], uuid=None) is None


def test_new_episode_lands_in_existing_program_with_approval_job(db_session):
    old = _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    t = _target(db_session)
    stats = _run(db_session, t, [_rapi_ep("u-new", "222")])
    assert stats.new == 1
    ep = db_session.query(Episode).filter_by(ext_id="222").one()
    assert ep.work_id == old.work_id
    assert ep.url == "https://portal.rozhlas.cz/222.mp3"
    assert ep.duration_ms == 495_000
    assert ep.summary == "Popis"
    assert ep.published_at is not None
    assert ep.availability_status == AvailabilityStatus.AVAILABLE


def test_known_content_id_is_not_duplicated(db_session):
    _existing(db_session, "https://radiozurnal.rozhlas.cz/leto-9648445", ext_id="222")
    t = _target(db_session)
    stats = _run(db_session, t, [_rapi_ep("u-new", "222")])
    assert stats.new == 0
    assert db_session.query(Episode).count() == 1


def test_known_uuid_is_not_duplicated(db_session):
    _existing(db_session, "https://croaod.cz/x.m3u8", ext_id="u-new")
    stats = _run(db_session, _target(db_session), [_rapi_ep("u-new", "222")])
    assert stats.new == 0


def test_url_only_legacy_episode_gets_ext_id_instead_of_duplicate(db_session):
    old = _existing(db_session, "https://www.mujrozhlas.cz/vyvar/nove-dil", title="Novy dil")
    stats = _run(db_session, _target(db_session), [_rapi_ep("u-new", "333", title="Nový díl")])
    assert stats.new == 0 and stats.linked == 1
    assert db_session.get(Episode, old.id).ext_id == "333"
    assert db_session.query(Episode).count() == 1


def test_serial_parts_group_into_serial_work(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    eps = [_rapi_ep(f"u{i}", str(500 + i), title="Kniha", serial="Autor: Kniha", part=i)
           for i in (1, 2)]
    stats = _run(db_session, _target(db_session), eps)
    assert stats.new == 2
    w = db_session.query(Work).filter_by(title="Autor: Kniha").one()
    assert sorted(e.episode_number for e in w.episodes) == [1, 2]
    assert w.expected_total == 3


def test_program_created_when_show_is_unknown(db_session):
    stats = _run(db_session, _target(db_session, name="Úplně nový pořad"),
                 [_rapi_ep("u1", "901")])
    assert stats.new == 1
    ep = db_session.query(Episode).filter_by(ext_id="901").one()
    assert ep.work.series.program.name == "Úplně nový pořad"


def test_second_run_costs_nothing_new(db_session):
    t = _target(db_session)
    _run(db_session, t, [_rapi_ep("u1", "901")])
    stats = _run(db_session, t, [_rapi_ep("u1", "901")])
    assert stats.new == 0
    assert db_session.query(Episode).count() == 1


def test_crawler_uses_rapi_for_review_and_skips_slow_path(db_session):
    from audiobiblio.acquire import crawler
    t = _target(db_session)
    stats = rapi_crawl.RapiCrawlStats(show_uuid=SHOW, new=1)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(rapi_crawl, "crawl_target_via_rapi", return_value=stats), \
         patch.object(crawler, "_crawl_url") as slow:
        assert crawler.crawl_target(t, session=db_session) == 0
    slow.assert_not_called()
    assert db_session.get(CrawlTarget, t.id).next_crawl_at is not None


def test_crawler_falls_back_when_show_unresolved(db_session):
    from audiobiblio.acquire import crawler
    t = _target(db_session)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(rapi_crawl, "crawl_target_via_rapi", return_value=None), \
         patch.object(crawler, "_crawl_url", return_value=0) as slow:
        crawler.crawl_target(t, session=db_session)
    slow.assert_called()


def test_auto_targets_never_take_rapi_path(db_session):
    from audiobiblio.acquire import crawler
    t = _target(db_session, mode=ApprovalMode.AUTO)
    with patch("audiobiblio.sources.pairing.ensure_pair"), \
         patch.object(rapi_crawl, "crawl_target_via_rapi") as fast, \
         patch.object(crawler, "_crawl_url", return_value=0):
        crawler.crawl_target(t, session=db_session)
    fast.assert_not_called()


def test_review_episodes_are_indexed_without_jobs(db_session):
    """User decision 2026-10-08: index only, never flood the approval queue."""
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    _run(db_session, _target(db_session), [_rapi_ep("u-new", "222")])
    assert db_session.query(DownloadJob).count() == 0


def test_shared_content_id_in_one_batch_creates_one_episode(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    stats = _run(db_session, _target(db_session),
                 [_rapi_ep("u1", "222"), _rapi_ep("u2", "222")])
    assert stats.new == 1 and stats.errors == 0


def test_repeated_legacy_title_is_never_linked(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/a", title="1. dil")
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/b", title="1. dil")
    stats = _run(db_session, _target(db_session), [_rapi_ep("u1", "444", title="1. díl")])
    assert stats.linked == 0 and stats.new == 1


def test_published_at_is_stored_as_utc(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    _run(db_session, _target(db_session), [_rapi_ep("u-new", "222")])
    ep = db_session.query(Episode).filter_by(ext_id="222").one()
    assert ep.published_at.hour == 11  # 13:00+02:00


def test_catch_all_program_is_never_the_target(db_session):
    old = _existing(db_session, "https://www.mujrozhlas.cz/modelari/stary-dil",
                    ext_id="111", program="mujrozhlas")
    _run(db_session, _target(db_session, url="https://www.mujrozhlas.cz/modelari",
                             name="Modeláři"), [_rapi_ep("u1", "222")])
    ep = db_session.query(Episode).filter_by(ext_id="222").one()
    assert ep.work.series.program.name == "Modeláři"
    assert ep.work_id != old.work_id


def test_expired_listing_entries_are_ignored(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    stats = _run(db_session, _target(db_session), [_rapi_ep("u-old", "777", live=False)])
    assert stats.new == 0
    assert db_session.query(Episode).filter_by(ext_id="777").count() == 0


def test_gone_episode_is_revived_when_live_again(db_session):
    old = _existing(db_session, "https://api.mujrozhlas.cz/episodes/u1", ext_id="222")
    old.availability_status = AvailabilityStatus.GONE
    db_session.flush()
    stats = _run(db_session, _target(db_session), [_rapi_ep("u1", "222")])
    assert stats.revived == 1 and stats.new == 0
    ep = db_session.get(Episode, old.id)
    assert ep.availability_status == AvailabilityStatus.AVAILABLE
    assert ep.url == "https://portal.rozhlas.cz/222.mp3"


def test_known_gone_still_expired_is_left_alone(db_session):
    old = _existing(db_session, "https://api.mujrozhlas.cz/episodes/u1", ext_id="222")
    old.availability_status = AvailabilityStatus.GONE
    db_session.flush()
    stats = _run(db_session, _target(db_session), [_rapi_ep("u1", "222", live=False)])
    assert (stats.new, stats.revived) == (0, 0)


class TestBackfill:
    """Deep history continues across crawls from a persisted cursor."""

    def _walks(self, *walks):
        from audiobiblio.sources.rapi import EpisodeWalk
        calls = []

        def fake(u, needs_action, **kw):
            calls.append(kw)
            return walks[len(calls) - 1]
        return calls, fake, EpisodeWalk

    def _go(self, db, fake, state):
        with patch.object(rapi_crawl, "resolve_show_uuid", return_value=SHOW), \
             patch.object(rapi_crawl, "fetch_recent_episodes", side_effect=fake), \
             patch.object(rapi_crawl.rapi_backfill_state, "load", side_effect=lambda: dict(state)), \
             patch.object(rapi_crawl.rapi_backfill_state, "save",
                          side_effect=lambda st: (state.clear(), state.update(st))):
            return rapi_crawl.crawl_target_via_rapi(db, _target(db))

    def test_first_crawl_starts_backfill_after_head(self, db_session):
        from audiobiblio.sources.rapi import EpisodeWalk
        state = {}
        calls, fake, _ = self._walks(EpisodeWalk([], 100, False), EpisodeWalk([], 300, False))
        self._go(db_session, fake, state)
        assert calls[1]["start_offset"] == 100  # an idle head still backfills once
        assert calls[1]["stop_when_idle"] is False
        assert state == {SHOW: 300}

    def test_backfill_continues_from_cursor_and_finishes(self, db_session):
        from audiobiblio.sources.rapi import EpisodeWalk
        state = {SHOW: 300}
        calls, fake, _ = self._walks(EpisodeWalk([], 100, False), EpisodeWalk([], 450, True))
        self._go(db_session, fake, state)
        assert calls[1]["start_offset"] == 300 - rapi_crawl.BACKFILL_OVERLAP
        assert state == {SHOW: rapi_crawl.rapi_backfill_state.DONE}

    def test_done_show_costs_one_walk(self, db_session):
        from audiobiblio.sources.rapi import EpisodeWalk
        state = {SHOW: rapi_crawl.rapi_backfill_state.DONE}
        calls, fake, _ = self._walks(EpisodeWalk([], 100, False))
        self._go(db_session, fake, state)
        assert len(calls) == 1

    def test_short_show_is_done_after_head(self, db_session):
        from audiobiblio.sources.rapi import EpisodeWalk
        state = {}
        calls, fake, _ = self._walks(EpisodeWalk([], 40, True))
        self._go(db_session, fake, state)
        assert len(calls) == 1 and state == {SHOW: rapi_crawl.rapi_backfill_state.DONE}


def test_gone_archive_stub_is_revived_not_duplicated(db_session):
    _existing(db_session, "https://www.mujrozhlas.cz/vyvar/leto", ext_id="111")
    stub = _existing(db_session, "https://junior.rozhlas.cz/pernikova-pohadka-1234567",
                     title="Pernikova pohadka. Vesely pribeh")
    stub.availability_status = AvailabilityStatus.GONE
    db_session.flush()
    stats = _run(db_session, _target(db_session),
                 [_rapi_ep("u1", "555", title="Perníková pohádka. Veselý příběh")])
    assert stats.new == 0 and stats.linked == 1
    ep = db_session.get(Episode, stub.id)
    assert ep.ext_id == "555" and ep.availability_status == AvailabilityStatus.AVAILABLE
    assert ep.url == "https://portal.rozhlas.cz/555.mp3"
