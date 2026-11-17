"""One rAPI serial = one work, whichever path ingests its parts.

Live failures (2026-11, works 120393/120403 Hrabal, 119036/119460 Prosecké
peklo): the HTML crawler named the work after the article page, show-sweep
after rAPI's serial title, serial-sweep attached parts to the crawler's stub —
three paths, three identity rules, so every title variant split the book and
the half-books waited for parts that sat in the other half forever.

Identity is now: the serial UUID when known, else the book key (author +
title head, subtitle ignored) — unless the existing work belongs to a
DIFFERENT serial UUID (a re-air is its own broadcast).
"""
from __future__ import annotations

import json
from unittest.mock import patch

from audiobiblio.acquire import rapi_crawl, serial_sweep
from audiobiblio.core.db.models import (
    ApprovalMode, Asset, AssetStatus, AssetType, CrawlTarget, CrawlTargetKind,
    Episode, Program, Series, Station, Work,
)

SERIAL = "89b4e081-8958-3742-b7e1-feb909a0487e"
OTHER_SERIAL = "11111111-2222-3333-4444-555555555555"
HRABAL_STUB = "Bohumil Hrabal: Taneční hodiny pro starší a pokročilé. Originální monolog zestárlého pábitele"
HRABAL_RAPI = ("Bohumil Hrabal: Taneční hodiny pro starší a pokročilé. "
               "Stylově originální monolog zestárlého vychloubačného pábitele")
PROSECKE_A = ("Petra Klabouchová: Prosecké peklo. Tajemný krimi příběh inspirovaný "
              "skutečnými událostmi i\xa0legendami")
PROSECKE_B = ("Petra Klabouchová: Prosecké peklo. Tajemný krimi příběh inspirovaný "
              "skutečnými událostmi i legendami proseckého podzemí")
PAGE = "https://dvojka.rozhlas.cz/cetba-na-pokracovani-7230785"
LINK = [{"variant": "hls", "duration": 100, "url": "https://croaod.cz/s/x.m3u8"}]


def _program(db, name="Četba na pokračování"):
    st = db.query(Station).filter_by(code="cro2").first() or Station(code="cro2", name="Dvojka")
    db.add(st)
    db.flush()
    p = Program(station_id=st.id, name=name)
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name=name)
    db.add(se)
    db.flush()
    return p, se


def _work(db, series, title, parts=(), serial_uuid=None, total=6, audio=False):
    w = Work(series_id=series.id, title=title, expected_total=total, serial_uuid=serial_uuid)
    db.add(w)
    db.flush()
    for n in parts:
        ep = Episode(work_id=w.id, title=f"díl {n}", episode_number=n,
                     ext_id=f"{serial_uuid or title[:8]}-{n}",
                     url=f"https://www.mujrozhlas.cz/rapi/view/episode/{n}")
        db.add(ep)
        db.flush()
        if audio:
            db.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                         status=AssetStatus.COMPLETE, file_path=f"/x/{w.id}-{n}.m4a"))
    db.flush()
    return w


def _rapi(part, serial_title, serial_uuid=SERIAL, uuid=None):
    return {"id": uuid or f"aaaaaaaa-0000-0000-0000-{part:012d}",
            "relationships": {"serial": {"data": {"type": "serial", "id": serial_uuid}}},
            "attributes": {"title": serial_title.split(". ")[0], "part": part,
                           "mirroredSerial": {"title": serial_title, "totalParts": 6},
                           "audioLinks": LINK}}


def _show_sweep(db, program_name, episodes):
    db.execute(__import__("sqlalchemy").text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES (:u, 'PROGRAM', :n, 1, 'AUTO', 24, datetime('now'))"),
        {"u": PAGE, "n": program_name})

    class _Resp:
        code = 301
        headers = {"Location": "https://www.mujrozhlas.cz/rapi/view/show/0038fee4-b218-3a73-a4a4-04544f431aad"}

        def read(self):
            return json.dumps({"data": episodes}).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db)


def _works(db, program):
    return db.query(Work).join(Series).filter(Series.program_id == program.id).all()


# --- show-sweep ---------------------------------------------------------------

def test_show_sweep_joins_crawler_stub_with_different_subtitle(db_session):
    """Hrabal: the HTML stub carries another subtitle than rAPI's serial."""
    p, se = _program(db_session)
    stub = _work(db_session, se, HRABAL_STUB, total=None)
    db_session.add(Episode(work_id=stub.id, title="Taneční hodiny", url=PAGE + "-hrabal"))
    db_session.flush()
    _show_sweep(db_session, p.name, [_rapi(1, HRABAL_RAPI), _rapi(2, HRABAL_RAPI)])
    assert [w.id for w in _works(db_session, p)] == [stub.id]
    assert stub.serial_uuid == SERIAL
    assert {e.episode_number for e in stub.episodes if e.ext_id} == {1, 2}


def test_late_parts_join_the_stamped_work_despite_other_subtitle(db_session):
    """Prosecké peklo: parts 5-6 aired days later under a longer subtitle."""
    p, se = _program(db_session)
    w = _work(db_session, se, PROSECKE_A, parts=(1, 2, 3, 4), serial_uuid=SERIAL)
    _show_sweep(db_session, p.name, [_rapi(5, PROSECKE_B), _rapi(6, PROSECKE_B)])
    assert [x.id for x in _works(db_session, p)] == [w.id]
    assert {e.episode_number for e in w.episodes} == {1, 2, 3, 4, 5, 6}


def test_late_parts_join_an_unstamped_legacy_work_and_stamp_it(db_session):
    p, se = _program(db_session)
    w = _work(db_session, se, PROSECKE_A, parts=(1, 2, 3, 4))
    _show_sweep(db_session, p.name, [_rapi(5, PROSECKE_B), _rapi(6, PROSECKE_B)])
    assert [x.id for x in _works(db_session, p)] == [w.id]
    assert w.serial_uuid == SERIAL


def test_reair_with_another_serial_uuid_is_its_own_work(db_session):
    """Same book aired again = another broadcast: never poured into the old work."""
    p, se = _program(db_session)
    old = _work(db_session, se, PROSECKE_A, parts=(1, 2, 3, 4, 5, 6), serial_uuid=OTHER_SERIAL)
    _show_sweep(db_session, p.name, [_rapi(1, PROSECKE_B, serial_uuid=SERIAL)])
    works = _works(db_session, p)
    assert len(works) == 2
    assert {e.episode_number for e in old.episodes} == {1, 2, 3, 4, 5, 6}
    new = next(w for w in works if w.id != old.id)
    assert new.serial_uuid == SERIAL


def test_serial_less_item_keeps_title_grouping(db_session):
    p, se = _program(db_session)
    item = {"id": "bbbbbbbb-0000-0000-0000-000000000001",
            "attributes": {"title": "Povídka dne", "audioLinks": LINK}}
    _show_sweep(db_session, p.name, [item])
    assert [w.title for w in _works(db_session, p)] == ["Povídka dne"]


# --- serial-sweep -------------------------------------------------------------

def _expand(db, serial, work_id, episodes):
    class _Resp:
        def read(self):
            return json.dumps({"data": episodes}).encode()

    with patch.object(serial_sweep, "_http", return_value=_Resp()), \
         patch.object(serial_sweep.time, "sleep"):
        return serial_sweep._expand_serial(db, serial, work_id)


def test_expand_serial_stamps_the_stub_work(db_session):
    p, se = _program(db_session)
    stub = _work(db_session, se, HRABAL_STUB, total=None)
    _expand(db_session, SERIAL, stub.id, [_rapi(1, HRABAL_RAPI)])
    assert stub.serial_uuid == SERIAL


def test_expand_serial_routes_parts_to_the_already_stamped_work(db_session):
    """show-sweep got there first (stamped work); the stub must not collect parts."""
    p, se = _program(db_session)
    canonical = _work(db_session, se, HRABAL_RAPI, parts=(1,), serial_uuid=SERIAL)
    stub = _work(db_session, se, HRABAL_STUB, total=None)
    _expand(db_session, SERIAL, stub.id, [_rapi(2, HRABAL_RAPI)])
    assert {e.episode_number for e in canonical.episodes} == {1, 2}
    assert stub.episodes == []


# --- rapi crawl ---------------------------------------------------------------

def test_rapi_crawl_serial_part_joins_stamped_work(db_session):
    from audiobiblio.sources.rapi import EpisodeWalk
    p, se = _program(db_session)
    w = _work(db_session, se, PROSECKE_A, parts=(1, 2, 3, 4), serial_uuid=SERIAL)
    t = CrawlTarget(url=PAGE, kind=CrawlTargetKind.PROGRAM, name=p.name, active=True,
                    interval_hours=24, approval_mode=ApprovalMode.AUTO)
    db_session.add(t)
    db_session.flush()
    eps = [_rapi(5, PROSECKE_B), _rapi(6, PROSECKE_B)]
    for e in eps:
        e["meta"] = {"ga": {"contentId": f"9{e['attributes']['part']}"}}
        e["attributes"]["since"] = "2026-11-08T00:01:00+01:00"
    store: dict = {}
    with patch.object(rapi_crawl, "resolve_show_uuid", return_value="s-1"), \
         patch.object(rapi_crawl, "fetch_recent_episodes",
                      side_effect=lambda u, na, **kw: EpisodeWalk([e for e in eps if na(e)], 2, True)), \
         patch.object(rapi_crawl.rapi_backfill_state, "load", side_effect=lambda: dict(store)), \
         patch.object(rapi_crawl.rapi_backfill_state, "save", side_effect=store.update):
        rapi_crawl.crawl_target_via_rapi(db_session, t)
    assert [x.id for x in _works(db_session, p)] == [w.id]
    assert {e.episode_number for e in w.episodes} == {1, 2, 3, 4, 5, 6}


# --- detector + merge of the existing splits ------------------------------------

def _two_split_books(db):
    p, se = _program(db)
    other = Series(program_id=p.id, name="Paula Hawkins: Dívka ve vlaku")
    db.add(other)
    db.flush()
    hrabal = _work(db, other, HRABAL_RAPI, parts=(1, 3, 4, 5, 6), serial_uuid=SERIAL, audio=True)
    hrabal_stub = _work(db, se, HRABAL_STUB, total=6)
    for n in (1, 2):
        db.add(Episode(work_id=hrabal_stub.id, title=f"h{n}", episode_number=n,
                       ext_id=f"stub-{n}", url=f"https://x/{n}"))
    prosecke = _work(db, se, PROSECKE_A, parts=(1, 2, 3, 4), audio=True)
    prosecke_late = _work(db, se, PROSECKE_B, parts=(5, 6), audio=True)
    db.flush()
    return p, hrabal, hrabal_stub, prosecke, prosecke_late


def test_detector_finds_both_live_splits_and_nothing_else(db_session):
    from audiobiblio.library.serial_identity import find_split_groups
    p, hrabal, hrabal_stub, prosecke, late = _two_split_books(db_session)
    _work(db_session, db_session.query(Series).filter_by(program_id=p.id).first(),
          "Karel Čapek: Válka s mloky", parts=(1, 2, 3, 4, 5, 6), audio=True)
    groups = find_split_groups(db_session)
    assert sorted(sorted(w.id for w in g.works) for g in groups) == sorted(
        [sorted([hrabal.id, hrabal_stub.id]), sorted([prosecke.id, late.id])])


def test_detector_ignores_complete_reair_copies(db_session):
    from audiobiblio.library.serial_identity import find_split_groups
    p, se = _program(db_session)
    _work(db_session, se, PROSECKE_A, parts=(1, 2, 3, 4, 5, 6), serial_uuid=OTHER_SERIAL)
    _work(db_session, se, PROSECKE_B, parts=(1, 2, 3, 4, 5, 6), serial_uuid=SERIAL)
    assert find_split_groups(db_session) == []


def test_merge_works_dry_run_changes_nothing(db_session, tmp_path):
    from audiobiblio.library.serial_identity import find_split_groups, merge_works
    _two_split_books(db_session)
    g = next(g for g in find_split_groups(db_session) if len(g.works) == 2
             and any(w.title == PROSECKE_A for w in g.works))
    before = db_session.query(Episode).count(), db_session.query(Work).count()
    actions = merge_works(db_session, g.canonical.id, g.duplicates[0].id, tmp_path, dry_run=True)
    assert actions
    assert (db_session.query(Episode).count(), db_session.query(Work).count()) == before


def test_merge_works_joins_parts_and_removes_empty_duplicate(db_session, tmp_path):
    from audiobiblio.library.serial_identity import find_split_groups, merge_works
    p, hrabal, hrabal_stub, prosecke, late = _two_split_books(db_session)
    for g in find_split_groups(db_session):
        for dup in g.duplicates:
            merge_works(db_session, g.canonical.id, dup.id, tmp_path, dry_run=False,
                        trash_fn=lambda path: path)
    db_session.flush()
    works = {w.id: w for w in _works(db_session, p)}
    assert late.id not in works or prosecke.id not in works          # one of the pair is gone
    keep_prosecke = works.get(prosecke.id) or works[late.id]
    assert sorted(e.episode_number for e in keep_prosecke.episodes) == [1, 2, 3, 4, 5, 6]
    keep_hrabal = works.get(hrabal.id) or works[hrabal_stub.id]
    assert sorted(e.episode_number for e in keep_hrabal.episodes) == [1, 2, 3, 4, 5, 6]
    assert hrabal_stub.id not in works


# --- the work's series is the program's own, never another book's ----------------

def test_new_serial_work_lands_in_the_programs_own_series(db_session):
    """Live: Hrabal sat under 'Paula Hawkins: Dívka ve vlaku' — both sweeps took
    the FIRST series of the program, which happened to be another book's."""
    st = Station(code="cro2", name="Dvojka")
    db_session.add(st)
    db_session.flush()
    p = Program(station_id=st.id, name="Četba na pokračování")
    db_session.add(p)
    db_session.flush()
    foreign = Series(program_id=p.id, name="Paula Hawkins: Dívka ve vlaku. Thriller")
    db_session.add(foreign)
    db_session.flush()
    own = Series(program_id=p.id, name=p.name)      # created later -> HIGHER id
    db_session.add(own)
    db_session.flush()
    assert foreign.id < own.id
    _show_sweep(db_session, p.name, [_rapi(1, HRABAL_RAPI)])
    w = _works(db_session, p)[0]
    assert w.series_id == own.id
