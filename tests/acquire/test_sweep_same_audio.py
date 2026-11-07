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
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, Episode, EpisodeAlias,
    UpgradeCandidate, UpgradeStatus,
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
