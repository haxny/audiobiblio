"""The crawler/gap-fill path must attach to a show-sweep episode of the same
audio instead of creating (and downloading) a second row.

Live case 2026-11-07: 'Dokumentarni povidky Milose Dolezala, Aleny Wagnerove
a Josefa Kopty' parts 3-5 existed twice in one work — rAPI UUID row from
show-sweep and numeric-id row from the crawler — same croaod stream, same
part, same duration; both were downloaded into the same file.
"""
from __future__ import annotations

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, AvailabilityStatus, DownloadJob, Episode,
    EpisodeAlias, JobStatus,
)
from audiobiblio.library.pipelines.ingest import (
    queue_assets_for_episode, upsert_from_item,
)
from audiobiblio.core.db.models import ApprovalMode

WORK = "Dokumentarni povidky Milose Dolezala, Aleny Wagnerove a Josefa Kopty"
STREAM = "https://croaod.cz/stream/26638c4a-35b2-3843-93ab-c0182c9a8d50.m4a/master.m3u8"
UUID = "26638c4a-35b2-3843-93ab-c0182c9a8d50"
NUMERIC = "12152327"


def _crawler_upsert(db, url=STREAM, ext_id=NUMERIC, ep_num=3, duration_ms=1_536_000,
                    title="Milos Dolezal: Kluk z Kamenice"):
    return upsert_from_item(
        db, url=url, item_title=title, series_name="Povidka", author=None,
        uploader=None, program_name="Povidka", work_title=WORK,
        episode_number=ep_num, ext_id=ext_id, duration_ms=duration_ms,
        source_url="https://vltava.rozhlas.cz/povidka-5012871",
    )


def _show_sweep_row(db, work_id, ep_num=3, duration_ms=1_536_000):
    ep = Episode(work_id=work_id, ext_id=UUID, title="Miloš Doležal: Kluk z Kamenice",
                 episode_number=ep_num, url=STREAM, duration_ms=duration_ms,
                 availability_status=AvailabilityStatus.AVAILABLE,
                 auto_download=True, priority=10, discovery_source="show-sweep")
    db.add(ep)
    db.flush()
    db.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                       status=JobStatus.PENDING, reason="show-sweep: povidka"))
    db.commit()
    return ep


def _work_id(db):
    # build the hierarchy exactly as the crawler would, then drop its episode
    ep, work = _crawler_upsert(db, url="https://croaod.cz/stream/seed.m3u8",
                               ext_id="seed", ep_num=1, duration_ms=1_000_000,
                               title="Seed")
    return work.id


def test_crawler_attaches_to_show_sweep_row_with_same_stream(db_session):
    wid = _work_id(db_session)
    sweep = _show_sweep_row(db_session, wid)

    ep, _ = _crawler_upsert(db_session)

    assert ep.id == sweep.id
    rows = db_session.query(Episode).filter_by(work_id=wid, episode_number=3).all()
    assert len(rows) == 1
    # rAPI identity and the richer (diacritic) title are kept
    assert ep.ext_id == UUID
    assert ep.title == "Miloš Doležal: Kluk z Kamenice"
    assert ep.discovery_source == "show-sweep"
    # numeric id recorded as an alias for future lookups
    alias = db_session.query(EpisodeAlias).filter_by(episode_id=ep.id,
                                                     ext_id=NUMERIC).first()
    assert alias is not None


def test_crawler_does_not_queue_second_audio_job(db_session):
    wid = _work_id(db_session)
    sweep = _show_sweep_row(db_session, wid)
    ep, _ = _crawler_upsert(db_session)
    queue_assets_for_episode(db_session, ep.id, approval_mode=ApprovalMode.AUTO)
    audio_jobs = db_session.query(DownloadJob).filter_by(
        episode_id=sweep.id, asset_type=AssetType.AUDIO).all()
    assert len(audio_jobs) == 1
    assert db_session.query(DownloadJob).join(Episode).filter(
        Episode.work_id == wid, Episode.episode_number == 3,
        DownloadJob.asset_type == AssetType.AUDIO).count() == 1


def test_crawler_never_requeues_complete_rapi_asset(db_session):
    wid = _work_id(db_session)
    sweep = _show_sweep_row(db_session, wid)
    db_session.query(DownloadJob).filter_by(episode_id=sweep.id).update(
        {"status": JobStatus.SUCCESS})
    db_session.add(Asset(episode_id=sweep.id, type=AssetType.AUDIO,
                         status=AssetStatus.COMPLETE, file_path="/lib/p3.m4a"))
    db_session.commit()
    ep, _ = _crawler_upsert(db_session)
    jobs = queue_assets_for_episode(db_session, ep.id, approval_mode=ApprovalMode.AUTO)
    assert all(j.asset_type != AssetType.AUDIO for j in jobs)


def test_crawler_attaches_by_part_and_duration_when_urls_differ(db_session):
    wid = _work_id(db_session)
    sweep = _show_sweep_row(db_session, wid, duration_ms=1_536_400)
    ep, _ = _crawler_upsert(db_session, url="https://croaod.cz/stream/other.m3u8",
                            duration_ms=1_536_000)
    assert ep.id == sweep.id


def test_crawler_attaches_via_content_id_alias(db_session):
    """Show-sweep records the contentId alias, so even a page URL from the
    crawler resolves to the same episode."""
    wid = _work_id(db_session)
    sweep = _show_sweep_row(db_session, wid)
    db_session.add(EpisodeAlias(episode_id=sweep.id, url=STREAM, ext_id=NUMERIC,
                                discovery_source="show-sweep"))
    db_session.commit()
    ep, _ = _crawler_upsert(db_session, url="https://www.mujrozhlas.cz/povidka/kluk",
                            duration_ms=None, ep_num=7)
    assert ep.id == sweep.id
    assert ep.url == STREAM  # a page URL is fine, but the stream stays


def test_different_part_still_creates_its_own_episode(db_session):
    wid = _work_id(db_session)
    _show_sweep_row(db_session, wid)
    ep, _ = _crawler_upsert(db_session, url="https://croaod.cz/stream/p4.m3u8",
                            ext_id="12152328", ep_num=4, duration_ms=1_480_000,
                            title="Part 4")
    assert ep.ext_id == "12152328"
    assert db_session.query(Episode).filter_by(work_id=wid).count() == 3
