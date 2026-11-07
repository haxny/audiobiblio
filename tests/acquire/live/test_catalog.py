"""A finished recording becomes Program/Series/Work/Episode + COMPLETE
AUDIO asset, the file lands under the right root and carries tags."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta

import pytest
from mutagen.mp4 import MP4

from audiobiblio.acquire.live.catalog import (
    build_comment, catalog_recording, composer_for, destination_for,
)
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, LiveRecording, LiveStatus, MetadataValue, Program,
    Station, Work,
)

T0 = datetime(2026, 10, 10, 5, 59)
ITEMS = [{"since": "2026-10-10T08:00:00+02:00", "till": "2026-10-10T12:00:00+02:00",
          "headline": "Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896) I.",
          "title": "Anton Bruckner: Symfonie č. 7 E dur. Hrají Vídeňští filharmonikové",
          "description": "Anton Bruckner: Symfonie č. 7 E dur. Hrají Vídeňští filharmonikové, řídí Herbert von Karajan."}]


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    music = tmp_path / "music"
    books = tmp_path / "books"
    import audiobiblio.acquire.live.catalog as cat
    monkeypatch.setattr(cat, "_roots", lambda: {"music": music, "spoken": books})
    return music, books


def _rec(db, **kw):
    d = dict(station_id="e26a14b0-35d5-3b55-a6c8-679d2c6e0526", station_code="CRoDDur", kind="block",
             dedupe_key="hudebni a duchovni svet antona brucknera 4 9 1824 11 10 1896 i",
             title=ITEMS[0]["headline"],
             series_title="Hudební a duchovní svět Antona Brucknera (4. 9. 1824 - 11.10. 1896)", part=1,
             schedule_start=T0 + timedelta(seconds=60), schedule_end=T0 + timedelta(hours=4, seconds=60),
             window_start=T0, window_end=T0 + timedelta(hours=4, minutes=3),
             stream_url="https://rozhlas.stream/ddur_high.aac", items=ITEMS,
             description=ITEMS[0]["description"], status=LiveStatus.FINALIZING, runs=[],
             duration_s=14580.0, coverage=0.99)
    d.update(kw)
    r = LiveRecording(**d)
    db.add(r)
    db.commit()
    return r


def test_composer_only_when_obvious():
    assert composer_for(ITEMS[0]["headline"], ITEMS) == "Anton Bruckner"
    assert composer_for("Průlet nad symfoniemi Dmitrije Šostakoviče I.",
                        [{"title": "Dmitrij Šostakovič: Symfonie č. 5"}]) == "Dmitrij Šostakovič"
    assert composer_for("Návštěva u Daniela Wiesnera I.",
                        [{"title": "Rozhovor s klavíristou, který rád hraje"}]) is None


def test_comment_has_description_and_playlist_with_times():
    c = build_comment("Pocta skladateli.", ITEMS, "Český rozhlas D-dur")
    assert c.startswith("Pocta skladateli.")
    assert "08:00–12:00" in c and "Anton Bruckner: Symfonie č. 7" in c
    assert "Český rozhlas D-dur, 10. 10. 2026" in c


def test_destination_music_vs_spoken(roots):
    music, books = roots
    assert destination_for("block", "CRoDDur", "Víkendové dopoledne") == music / "Vikendove dopoledne (CRo)"
    assert destination_for("serial", "CRoPlus", "Záznamy z vysílání") == books / "Zaznamy z vysilani (CRo)"


def test_catalog_creates_rows_moves_file_and_tags(db_session, roots, silent_m4a):
    music, _ = roots
    st = Station(code="CRoDDur", name="D-dur")
    db_session.add(st); db_session.flush()
    existing = Program(station_id=st.id, name="Vikendove dopoledne na stanici d dur")
    db_session.add(existing); db_session.commit()

    rec = _rec(db_session)
    ep = catalog_recording(db_session, rec, silent_m4a)
    db_session.commit()

    assert ep.discovery_source == "live-recorder"
    assert ep.ext_id.startswith("live:CRoDDur:")
    assert ep.episode_number == 1
    assert ep.published_at == rec.schedule_start
    assert ep.duration_ms == 14580000
    work = db_session.get(Work, ep.work_id)
    assert work.title == rec.series_title and work.author == "Anton Bruckner"
    assert work.series.program.id == existing.id          # reuses the known D-dur program
    asset = db_session.query(Asset).filter_by(episode_id=ep.id, type=AssetType.AUDIO).one()
    assert asset.status == AssetStatus.COMPLETE
    assert asset.extra["live_recording_id"] == rec.id and asset.extra["coverage"] == 0.99
    assert asset.file_path.startswith(str(music / "Vikendove dopoledne (CRo)"))
    assert asset.file_path.endswith(".m4a")
    assert not silent_m4a.exists()                          # moved, not copied
    assert rec.episode_id == ep.id and rec.file_path == asset.file_path

    tags = MP4(asset.file_path).tags
    assert tags["\xa9alb"] == ["Hudebni a duchovni svet Antona Brucknera (4. 9. 1824 - 11.10. 1896)"]
    assert tags["\xa9nam"] == ["Hudebni a duchovni svet Antona Brucknera (4. 9. 1824 - 11.10. 1896) I."]
    assert tags["\xa9wrt"] == ["Anton Bruckner"]
    assert tags["\xa9ART"] == ["Anton Bruckner"]
    genre = [bytes(g).decode() for g in tags["----:com.apple.iTunes:GENRE"]]
    assert genre == ["Vikendove dopoledne (CRoDDur)"]
    assert "08:00–12:00" in tags["\xa9cmt"][0]
    assert tags["trkn"] == [(1, 0)]

    fields = {m.field: m.value for m in db_session.query(MetadataValue).filter_by(source="live-recorder")}
    assert fields["genre"] == "Vikendove dopoledne (CRoDDur)"
    assert fields["author"] == "Anton Bruckner"


def test_catalog_again_with_better_coverage_replaces_file_same_episode(db_session, roots,
                                                                      silent_m4a_factory):
    first, second = silent_m4a_factory("a.m4a"), silent_m4a_factory("b.m4a")
    rec1 = _rec(db_session, coverage=0.5, status=LiveStatus.PARTIAL)
    ep1 = catalog_recording(db_session, rec1, first)
    db_session.commit()
    rec2 = _rec(db_session, window_start=T0 + timedelta(hours=18), coverage=0.99,
                schedule_start=T0 + timedelta(hours=18, seconds=60))
    ep2 = catalog_recording(db_session, rec2, second)
    db_session.commit()
    assert ep1.id == ep2.id
    assert db_session.query(Episode).count() == 1
    asset = db_session.query(Asset).filter_by(episode_id=ep1.id, type=AssetType.AUDIO).one()
    assert asset.extra["coverage"] == 0.99 and asset.extra["live_recording_id"] == rec2.id


def test_catalog_worse_coverage_keeps_existing_file(db_session, roots, silent_m4a_factory):
    first, second = silent_m4a_factory("a.m4a"), silent_m4a_factory("b.m4a")
    ep = catalog_recording(db_session, _rec(db_session, coverage=0.99), first)
    db_session.commit()
    rec2 = _rec(db_session, window_start=T0 + timedelta(hours=18), coverage=0.4,
                schedule_start=T0 + timedelta(hours=18, seconds=60))
    catalog_recording(db_session, rec2, second)
    asset = db_session.query(Asset).filter_by(episode_id=ep.id, type=AssetType.AUDIO).one()
    assert asset.extra["coverage"] == 0.99
    assert second.exists()  # left in the work dir, not moved over the better file


def test_serial_part_goes_to_spoken_root(db_session, roots, silent_m4a):
    _, books = roots
    rec = _rec(db_session, station_id="c639d927-f37b-3db8-a64f-1d64ee1469b2", station_code="CRoPlus",
               kind="serial", dedupe_key="urgent#5", title="Urgent 5/6", series_title="Urgent",
               part=5, total_parts=6,
               items=[{"since": "2026-11-03T03:04:00+01:00", "till": "2026-11-03T03:27:00+01:00",
                       "headline": "Urgent 5/6", "title": "Vybíráme z úspěšných pořadů ČRo Plus",
                       "description": "Urgent - 5. díl - Kryštof 6"}],
               description="Urgent - 5. díl - Kryštof 6")
    ep = catalog_recording(db_session, rec, silent_m4a)
    work = db_session.get(Work, ep.work_id)
    assert work.title == "Urgent" and work.expected_total == 6
    asset = db_session.query(Asset).filter_by(episode_id=ep.id).one()
    assert asset.file_path.startswith(str(books / "Zaznamy z vysilani (CRo)"))
    tags = MP4(asset.file_path).tags
    assert bytes(tags["----:com.apple.iTunes:GENRE"][0]).decode() == "Zaznamy z vysilani (CRoPlus)"
    assert "\xa9wrt" not in tags
    _ = shutil


@pytest.mark.parametrize("kind,program,genre,folder", [
    ("block", "Speciály na D-dur", "Specialy na D-dur (CRoDDur)", "Specialy na D-dur (CRo)"),
    ("serial", "Z redakčního archivu", "Z redakcniho archivu (CRoDDur)", "Z redakcniho archivu (CRo)"),
])
def test_program_name_on_row_drives_genre_and_folder(db_session, roots, silent_m4a,
                                                     kind, program, genre, folder):
    music, _ = roots
    rec = _rec(db_session, kind=kind, program_name=program, title="Pocta Václavu Smetáčkovi",
               series_title="Pocta Václavu Smetáčkovi", part=None, dedupe_key="pocta")
    ep = catalog_recording(db_session, rec, silent_m4a)
    work = db_session.get(Work, ep.work_id)
    assert work.series.program.name == program
    asset = db_session.query(Asset).filter_by(episode_id=ep.id).one()
    assert asset.file_path.startswith(str(music / folder))
    assert bytes(MP4(asset.file_path).tags["----:com.apple.iTunes:GENRE"][0]).decode() == genre
