"""Serials embedded in a program (Počteníčko, Dobré dopoledne): a show page
lists episodes of several serials. Work identity = the rAPI serial, part
number = the rAPI part — never the show title and the playlist index
(live: works/22423 'Počteníčko' mixed two books, Uhni se part 1 was #10).
Program identity = the program URL (live: two 'Dobré dopoledne' programs).
"""
from __future__ import annotations

import audiobiblio.acquire.crawler as crawler_mod
from audiobiblio.acquire import rapi_crawl
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, CrawlTargetKind, Episode, Program, Station, Work,
)
from audiobiblio.library.pipelines.ingest import upsert_from_item
from audiobiblio.sources.mrz_inspector import EpisodeItem, ProbeResult
from audiobiblio.sources.rapi import EpisodeWalk

SHOW_URL = "https://www.mujrozhlas.cz/poctenicko"


def _rapi_ep(uuid, cid, serial, part, total):
    return {"id": uuid, "meta": {"ga": {"contentId": cid}},
            "attributes": {"part": part,
                           "mirroredSerial": {"title": serial, "totalParts": total} if serial else None}}


def test_serial_map_keys_both_ids(monkeypatch):
    monkeypatch.setattr(rapi_crawl, "resolve_show_uuid", lambda urls, name=None: "show-1")
    monkeypatch.setattr(rapi_crawl, "fetch_recent_episodes", lambda *a, **k: EpisodeWalk(
        [_rapi_ep("u1", 501, "Uhni se, topí se", 2, 3), _rapi_ep("u2", 502, None, None, None)], 2, True))
    t = CrawlTarget(url=SHOW_URL, kind=CrawlTargetKind.PROGRAM, name="Počteníčko")
    m = rapi_crawl.serial_map(t)
    assert m["501"] == m["u1"] == {"title": "Uhni se, topí se", "total": 3, "part": 2}
    assert "502" not in m


def test_show_crawl_groups_by_serial(db_session, monkeypatch):
    target = CrawlTarget(url=SHOW_URL, kind=CrawlTargetKind.PROGRAM, name="Počteníčko",
                         approval_mode=ApprovalMode.AUTO, interval_hours=24)
    db_session.add(target); db_session.commit()
    entries = [
        EpisodeItem(url=f"{SHOW_URL}/palec-{i}", title=f"Palec ve stínu {i}", ext_id=str(100 + i))
        for i in (1, 2)
    ] + [EpisodeItem(url=f"{SHOW_URL}/uhni", title="Uhni se, topí se (2/3)", ext_id="501")]
    show_pr = ProbeResult(kind="program", url=SHOW_URL, title="Počteníčko", series=None,
                          uploader="mujrozhlas", extractor="mujrozhlas", entries=entries)
    monkeypatch.setattr(crawler_mod, "probe_url", lambda url: {"stub": True})
    monkeypatch.setattr(crawler_mod, "classify_probe", lambda data, url: show_pr)
    monkeypatch.setattr(crawler_mod, "_discover_entries", lambda pr, url: pr.entries)
    monkeypatch.setattr(crawler_mod, "_rapi_serials", lambda t: {
        "101": {"title": "Palec ve stínu", "total": 8, "part": 1},
        "102": {"title": "Palec ve stínu", "total": 8, "part": 2},
        "501": {"title": "Uhni se, topí se", "total": 3, "part": 2},
    })
    monkeypatch.setattr(crawler_mod, "queue_assets_for_episode", lambda *a, **k: [])
    crawler_mod.crawl_target(target, session=db_session)

    works = {w.title: w for w in db_session.query(Work).all()}
    assert "Počteníčko" not in works, works.keys()
    uhni = works["Uhni se, topí se"]
    assert uhni.expected_total == 3 and uhni.expected_source == "rapi"
    assert [e.episode_number for e in uhni.episodes] == [2]
    assert sorted(e.episode_number for e in works["Palec ve stínu"].episodes) == [1, 2]
    prog = uhni.series.program
    assert prog.name == "Počteníčko" and prog.url == SHOW_URL


def test_program_matched_by_url_before_name(db_session):
    st = Station(code="CRoOl", name="Olomouc"); db_session.add(st); db_session.flush()
    url = "https://olomouc.rozhlas.cz/dobre-dopoledne-123"
    db_session.add(Program(station_id=st.id, name="Dobré dopoledne s Českým rozhlasem Olomouc", url=url))
    db_session.flush()
    ep, work = upsert_from_item(
        db_session, url="https://olomouc.rozhlas.cz/kasna-1", item_title="Kašna 1",
        series_name=None, author=None, uploader=None, program_name="Dobré dopoledne",
        program_url=url + "/", source_url="https://olomouc.rozhlas.cz/kasna-1",
        work_title="Za příběhy olomouckých kašen", episode_number=1, ext_id="9001")
    assert db_session.query(Program).count() == 1
    assert work.series.program.url == url
