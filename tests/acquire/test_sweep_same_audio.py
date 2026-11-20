"""Show-sweep / serial-sweep: one audio = one episode.

Live case 2026-11-07 ('Dokumentarni povidky ...', program Povidka): the
sweep keyed parts by rAPI UUID, the crawler by numeric contentId, and each
part was created and downloaded twice into the same file. Part 1 owned only
via the crawler was the ad variant (1552.7 s vs clean 1536.4 s).
"""
from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy import text

from audiobiblio.acquire import serial_sweep
from audiobiblio.dedupe.identity import find_same_audio, held_by_other_book
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, Episode, EpisodeAlias,
    JobStatus, UpgradeCandidate, UpgradeStatus, Work,
)
from tests.acquire.test_serial_sweep import _mk_stub

TARGET = "https://vltava.rozhlas.cz/povidka-5012871"
STREAM = "https://croaod.cz/stream/26638c4a.m4a/master.m3u8"
UUID = "26638c4a-35b2-3843-93ab-c0182c9a8d50"
CID = "12152327"
SERIAL = "Dokumentární povídky"


def _setup(db):
    w, stub = _mk_stub(db, program_name="Povidka", url=TARGET)
    w.title = SERIAL
    db.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES (:u, 'PROGRAM', 'Povidka', 1, 'AUTO', "
        "24, datetime('now'))"), {"u": TARGET})
    db.flush()
    return w


class _Resp:
    code = 301
    headers = {"Location": "https://www.mujrozhlas.cz/rapi/view/show/0038fee4-b218-3a73-a4a4-04544f431aad"}

    def __init__(self, items):
        self.items = items

    def read(self):
        return json.dumps({"data": self.items}).encode()


def _run_sweep(db, items):
    with patch.object(serial_sweep, "_http", return_value=_Resp(items)), \
         patch.object(serial_sweep.time, "sleep"):
        return serial_sweep.run_show_sweep(db)


def _item(duration=1536, cid=None, url=STREAM, part=3):
    item = {"id": UUID,
            "attributes": {"title": "Miloš Doležal: Kluk", "part": part,
                           "mirroredSerial": {"title": SERIAL},
                           "audioLinks": [{"variant": "hls", "duration": duration,
                                           "url": url}]}}
    if cid:
        item["meta"] = {"ga": {"contentId": int(cid)}}
    return item


def _crawled(db, w, *, part, url, duration_ms, complete=False):
    ep = Episode(work_id=w.id, ext_id=CID, title="Milos Dolezal: Kluk",
                 episode_number=part, url=url, duration_ms=duration_ms)
    db.add(ep)
    db.flush()
    if complete:
        db.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                     status=AssetStatus.COMPLETE, file_path=f"/lib/p{part}.m4a"))
    db.commit()
    return ep


def test_show_sweep_records_content_id_alias(db_session):
    """The crawler keys the same audio by contentId — the sweep leaves that
    id on its row so the crawler finds it (root of the duplicates)."""
    _setup(db_session)
    _run_sweep(db_session, [_item(cid=CID)])
    ep = db_session.query(Episode).filter_by(ext_id=UUID).one()
    assert db_session.query(EpisodeAlias).filter_by(
        episode_id=ep.id, ext_id=CID).count() == 1


def test_show_sweep_attaches_to_crawler_row_with_same_stream(db_session):
    """Crawler row first, rAPI payload without contentId: the same stream
    URL means the same audio → alias, no new row, no job."""
    w = _setup(db_session)
    crawled = _crawled(db_session, w, part=3, url=STREAM, duration_ms=1_536_000)
    stats = _run_sweep(db_session, [_item()])
    assert stats["new_episodes"] == 0
    assert db_session.query(Episode).filter_by(ext_id=UUID).count() == 0
    assert db_session.query(DownloadJob).filter_by(episode_id=crawled.id).count() == 0
    assert db_session.query(EpisodeAlias).filter_by(
        episode_id=crawled.id, ext_id=UUID).count() == 1


def test_show_sweep_flags_owned_ad_variant_for_replacement(db_session):
    """Owned copy is the 16 s longer ad variant; rAPI offers the clean one →
    pending pair (UpgradeCandidate), never a second row or download."""
    w = _setup(db_session)
    crawled = _crawled(db_session, w, part=1, url="https://www.mujrozhlas.cz/povidka/kluk",
                       duration_ms=1_552_700, complete=True)
    _run_sweep(db_session, [_item(duration=1536, cid=CID, part=1)])
    cand = db_session.query(UpgradeCandidate).filter_by(episode_id=crawled.id).one()
    assert cand.status == UpgradeStatus.PENDING_REVIEW
    assert cand.candidate_duration_ms == 1_536_000
    assert cand.owned_duration_ms == 1_552_700
    assert db_session.query(Episode).filter_by(ext_id=UUID).count() == 0
    assert db_session.query(DownloadJob).count() == 0
    _run_sweep(db_session, [_item(duration=1536, cid=CID, part=1)])  # idempotent
    assert db_session.query(UpgradeCandidate).filter_by(episode_id=crawled.id).count() == 1


def test_show_sweep_clean_owned_copy_needs_no_replacement(db_session):
    w = _setup(db_session)
    _crawled(db_session, w, part=1, url=STREAM, duration_ms=1_536_400, complete=True)
    _run_sweep(db_session, [_item(duration=1536, cid=CID, part=1)])
    assert db_session.query(UpgradeCandidate).count() == 0


def test_expand_serial_attaches_to_existing_same_stream(db_session):
    w, _ = _mk_stub(db_session)
    crawled = Episode(work_id=w.id, ext_id=CID, title="Kniha X 1",
                      episode_number=1, url="https://croaod.cz/s/x.m3u8")
    db_session.add(crawled)
    db_session.commit()
    items = [{"id": "11111111-1111-1111-1111-111111111111",
              "attributes": {"title": "Kniha X", "part": 1,
                             "audioLinks": [{"variant": "hls", "duration": 100,
                                             "url": "https://croaod.cz/s/x.m3u8"}]}}]
    with patch.object(serial_sweep, "_http", return_value=_Resp(items)):
        assert serial_sweep._expand_serial(db_session, "serial-uuid", w.id) == 0
    assert db_session.query(DownloadJob).filter_by(episode_id=crawled.id).count() == 0
    assert db_session.query(EpisodeAlias).filter_by(
        episode_id=crawled.id, ext_id="11111111-1111-1111-1111-111111111111").count() == 1


# --- the same recording in ANOTHER book is another cycle, not "already known" --

def _stray_in_other_book(db, w, *, url=STREAM, part=1):
    other = Work(series_id=w.series_id, title="Bohous svine (starsi vysilani)")
    db.add(other)
    db.flush()
    stray = Episode(work_id=other.id, ext_id=CID, title="Bohous svine",
                    episode_number=part, url=url, duration_ms=1_536_000)
    db.add(stray)
    db.commit()
    return other, stray


def test_show_sweep_ingests_part_owned_by_another_book(db_session):
    """Live 2026-11 (Doležal, part 2 'Bohouš svině'): its contentId belonged
    to a stray episode of another work, so the sweep skipped it as known and
    the book stayed one part short. It must get its own episode + download;
    the other book's episode stays untouched."""
    w = _setup(db_session)
    other, stray = _stray_in_other_book(db_session, w)
    stats = _run_sweep(db_session, [_item(cid=CID, part=2)])
    assert stats["new_episodes"] == 1
    new = db_session.query(Episode).filter_by(ext_id=UUID).one()
    assert new.work_id == w.id and new.episode_number == 2
    assert db_session.query(DownloadJob).filter_by(episode_id=new.id).count() == 1
    assert db_session.get(Episode, stray.id).work_id == other.id
    assert db_session.query(DownloadJob).filter_by(episode_id=stray.id).count() == 0


def test_show_sweep_ingests_when_only_the_stream_url_is_shared(db_session):
    """No contentId in the payload, same croaod stream as another book's
    episode: still its own episode (compared after download)."""
    w = _setup(db_session)
    _stray_in_other_book(db_session, w, part=2)
    stats = _run_sweep(db_session, [_item(part=2)])
    assert stats["new_episodes"] == 1
    assert db_session.query(Episode).filter_by(ext_id=UUID).one().work_id == w.id


def test_second_sweep_after_cross_book_ingest_is_idempotent(db_session):
    w = _setup(db_session)
    _stray_in_other_book(db_session, w)
    _run_sweep(db_session, [_item(cid=CID, part=2)])
    stats = _run_sweep(db_session, [_item(cid=CID, part=2)])
    assert stats["new_episodes"] == 0
    assert db_session.query(Episode).filter_by(ext_id=UUID).count() == 1


def test_same_book_match_is_still_deduplicated(db_session):
    """The routine case is unchanged: the crawler row of THIS book is not
    duplicated (CID in the same work)."""
    w = _setup(db_session)
    _crawled(db_session, w, part=3, url=STREAM, duration_ms=1_536_000)
    stats = _run_sweep(db_session, [_item(cid=CID)])
    assert stats["new_episodes"] == 0


# --- repeats are downloaded under a daily budget -------------------------------

def _many_items(n):
    return [{"id": f"00000000-0000-3000-8000-{i:012d}",
             "attributes": {"title": f"Dil {i}", "part": i,
                            "mirroredSerial": {"title": SERIAL},
                            "audioLinks": [{"variant": "hls", "duration": 1000 + i,
                                            "url": f"https://croaod.cz/stream/x{i}.m4a/master.m3u8"}]},
             "meta": {"ga": {"contentId": 9_000_000 + i}}} for i in range(1, n + 1)]


def _strays(db, w, n):
    other = Work(series_id=w.series_id, title="Starsi cyklus")
    db.add(other)
    db.flush()
    for i in range(1, n + 1):
        db.add(Episode(work_id=other.id, ext_id=str(9_000_000 + i), title=f"Dil {i}",
                       episode_number=i, url=f"https://croaod.cz/stream/x{i}.m4a/master.m3u8",
                       duration_ms=(1000 + i) * 1000))
    db.commit()


def test_repeats_over_the_daily_budget_wait_for_approval(db_session):
    """Live dry-run 2026-11-20: 474 repeats (~18 GB) would have been queued at
    once onto a nearly full disk. Within the budget they download, the rest is
    parked — episodes are still created, nothing is lost."""
    w = _setup(db_session)
    _strays(db_session, w, 35)
    stats = _run_sweep(db_session, _many_items(35))
    assert stats["new_episodes"] == 35 and stats["repeats"] == 35
    assert stats["repeats_deferred"] == 5
    jobs = db_session.query(DownloadJob).filter(DownloadJob.reason.like("repeat:%"))
    assert jobs.filter(DownloadJob.status == JobStatus.PENDING).count() == 30
    assert jobs.filter(DownloadJob.status == JobStatus.APPROVAL).count() == 5


def test_genuinely_new_episodes_are_not_budgeted(db_session):
    _setup(db_session)
    stats = _run_sweep(db_session, _many_items(35))
    assert stats["new_episodes"] == 35 and stats["repeats"] == 0
    assert db_session.query(DownloadJob).filter(
        DownloadJob.status == JobStatus.PENDING,
        DownloadJob.reason.notlike("repeat:%")).count() == 35


def test_crawler_matching_stays_global_unless_asked(db_session):
    """find_same_audio keeps its old global behaviour by default (the HTML
    crawler relies on it); only the sweeps opt in to book-scoped matching."""
    w = _setup(db_session)
    other, stray = _stray_in_other_book(db_session, w)
    kw = dict(url=None, ext_ids={CID}, work_id=w.id, episode_number=1, duration_ms=None)
    assert find_same_audio(db_session, **kw)[0].id == stray.id
    assert find_same_audio(db_session, same_book_only=True, **kw)[0] is None
    assert held_by_other_book(db_session, url=None, ext_ids={CID}, work_id=w.id,
                              episode_number=1) is True
    assert held_by_other_book(db_session, url=None, ext_ids={CID}, work_id=other.id,
                              episode_number=1) is False
