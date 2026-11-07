"""A re-aired part belongs to its new serial too (live case 2026-11):
'Miloš Doležal: Bohouš svině' (ext 12151052) first aired 2026-07 as a
one-episode Radiokniha work; in 2026-11 it re-aired as part 2 of the Vltava
serial 'Dokumentární povídky…' (parts 12151051, 12152327-29). ext_id is
unique, so the serial never got its part 2 and showed a gap."""
from __future__ import annotations

from pathlib import Path

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, Episode, Work,
)
from audiobiblio.library.pipelines.completeness import (
    complete_audio_count, work_completeness,
)
from audiobiblio.library.pipelines.finalize import finalize_work
from audiobiblio.library.pipelines.ingest import upsert_from_item
from audiobiblio.library.pipelines.shared_parts import shared_episodes, shared_part_ids

SERIAL = "Dokumentarni povidky Milose Dolezala, Aleny Wagnerove a Josefa Kopty"
PARTS = {1: "12151051", 2: "12151052", 3: "12152327", 4: "12152328", 5: "12152329"}


def _radiokniha(db):
    ep, work = upsert_from_item(
        db, url="https://www.mujrozhlas.cz/radiokniha/milos-dolezal-bohous-svine",
        item_title="Milos Dolezal: Bohous svine", series_name="Radiokniha",
        author=None, uploader=None, program_name="Radiokniha",
        work_title="Milos Dolezal: Bohous svine", episode_number=None,
        ext_id=PARTS[2])
    db.commit()
    return ep, work


def _serial_part(db, n):
    return upsert_from_item(
        db, url=f"https://www.mujrozhlas.cz/povidka/dokumentarni-povidky-{n}",
        item_title=f"Dokumentarni povidky {n}", series_name="Povidka",
        author=None, uploader=None, program_name="Povidka",
        work_title=SERIAL, episode_number=n, ext_id=PARTS[n])


def _serial_work(db):
    return db.query(Work).filter_by(title=SERIAL).one()


def test_reaired_part_is_linked_into_new_serial(db_session):
    old_ep, old_work = _radiokniha(db_session)
    for n in range(1, 6):
        _serial_part(db_session, n)
    db_session.commit()

    serial = _serial_work(db_session)
    assert shared_part_ids(serial) == {2: old_ep.id}
    # one episode, still in its first work — no duplicate download
    assert db_session.query(Episode).filter_by(ext_id=PARTS[2]).count() == 1
    db_session.refresh(old_ep)
    assert old_ep.work_id == old_work.id
    assert db_session.query(DownloadJob).filter_by(episode_id=old_ep.id).count() == 0
    assert [e.id for e in shared_episodes(db_session, serial)] == [old_ep.id]


def test_link_made_on_a_later_crawl_when_part_came_first(db_session):
    """Part 2 processed before the serial has own parts: nothing to attach
    to yet (could be a plain re-listing of the same book); the next crawl
    of the serial links it."""
    old_ep, _ = _radiokniha(db_session)
    _serial_part(db_session, 2)
    db_session.commit()
    assert db_session.query(Work).filter_by(title=SERIAL).count() == 0  # shell dropped
    for n in (1, 3, 4, 5):
        _serial_part(db_session, n)
    _serial_part(db_session, 2)
    db_session.commit()
    assert shared_part_ids(_serial_work(db_session)) == {2: old_ep.id}


def test_no_link_when_serial_already_has_its_own_part(db_session):
    _radiokniha(db_session)
    _serial_part(db_session, 1)
    own = Episode(work_id=_serial_work(db_session).id, title="vlastni 2",
                  episode_number=2, ext_id="999")
    db_session.add(own)
    db_session.commit()
    _serial_part(db_session, 2)
    db_session.commit()
    assert shared_part_ids(_serial_work(db_session)) == {}


def _complete(db, ep, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    db.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                 status=AssetStatus.COMPLETE, file_path=str(path)))
    db.flush()


def _downloaded_serial(db, tmp_path):
    old_ep, _ = _radiokniha(db)
    _complete(db, old_ep, tmp_path / "radiokniha" / "Bohous svine.m4a")
    for n in range(1, 6):
        ep, _ = _serial_part(db, n)
        if n != 2:
            _complete(db, ep, tmp_path / "povidka" / f"part{n}.m4a")
    serial = _serial_work(db)
    serial.expected_total = 5
    serial.expected_source = "rapi"
    db.commit()
    return serial, old_ep


def test_completeness_counts_the_shared_part(db_session, tmp_path):
    serial, _ = _downloaded_serial(db_session, tmp_path)
    assert complete_audio_count(db_session, serial.id) == 5
    comp = work_completeness(db_session, serial)
    assert comp.have == 5
    assert comp.missing_numbers == []


def test_finalize_places_shared_part_without_moving_it(db_session, tmp_path):
    serial, old_ep = _downloaded_serial(db_session, tmp_path)
    dest = tmp_path / "shelf" / "Kniha"
    report = finalize_work(db_session, serial, tmp_path, dry_run=False,
                           dest_dir_override=dest, book_stem="Dolezal - Povidky")
    assert not report.errors
    assert (dest / "Dolezal - Povidky - 02.m4a").is_file()
    # the shared file still belongs to (and stays with) its first work
    src = tmp_path / "radiokniha" / "Bohous svine.m4a"
    assert src.is_file()
    old_asset = db_session.query(Asset).filter_by(episode_id=old_ep.id).one()
    assert old_asset.file_path == str(src)
    assert len(list(dest.glob("*.m4a"))) == 5
