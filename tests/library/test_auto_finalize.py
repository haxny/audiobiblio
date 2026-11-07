"""The librarian: finished books shelve themselves (spec 2026-07-22)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, AvailabilityStatus, Episode, FieldOrigin,
    MetadataValue, Program, Series, Station, Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.library.pipelines import auto_finalize as af


NOW = datetime(2026, 7, 22)


@pytest.fixture()
def book(db_session, tmp_path, monkeypatch):
    monkeypatch.setitem(af.DESTINATIONS, "cetba na pokracovani",
                        (str(tmp_path / "fiction"), "book"))
    st = Station(code="CRo2", name="Dvojka"); db_session.add(st); db_session.flush()
    prog = Program(station_id=st.id, name="Četba na pokračování")
    db_session.add(prog); db_session.flush()
    ser = Series(program_id=prog.id, name="Četba na pokračování")
    db_session.add(ser); db_session.flush()
    w = Work(series_id=ser.id, title="Testkniha", author="Jan Autor", year=2020)
    db_session.add(w); db_session.flush()
    for n in (1, 2):
        ep = Episode(work_id=w.id, title=f"d{n}", episode_number=n,
                     published_at=NOW - timedelta(days=30))
        db_session.add(ep); db_session.flush()
        f = tmp_path / "lib" / f"{n:02d}.mp3"
        f.parent.mkdir(exist_ok=True); f.write_bytes(b"x")
        db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                             status=AssetStatus.COMPLETE, file_path=str(f)))
        record_value(db_session, "episode", ep.id, "narrator", "Petr Cteci",
                     FieldOrigin.SCRAPED, "t")
    db_session.flush()
    return w


def test_finished_book_is_shelved(db_session, tmp_path, book):
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any("SHELVE" in r for r in report), report
    dest = (tmp_path / "fiction" / "Jan Autor [audio]"
            / "Jan Autor - (2020) Testkniha (cte Petr Cteci, CRo 2026)")
    # audio files take the user convention "{Autor} - ({rok}) {Titul} - NN [nazev dilu]"
    assert (dest / "Jan Autor - (2020) Testkniha - 01 d1.mp3").exists()
    marker = db_session.query(MetadataValue).filter_by(
        entity_type="work", entity_id=book.id, field="final_path").one()
    assert str(dest) == marker.value


def test_idempotent_second_run(db_session, tmp_path, book):
    af.run_auto_finalize(db_session, now=NOW)
    report2 = af.run_auto_finalize(db_session, now=NOW)
    assert not any("SHELVE" in r for r in report2)


def test_fresh_book_waits_quiet_period(db_session, tmp_path, book):
    for e in book.episodes:
        e.published_at = NOW - timedelta(days=3)
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert report == []


def test_gone_part_blocks_when_total_unknown(db_session, tmp_path, book):
    book.episodes[0].availability_status = AvailabilityStatus.GONE
    db_session.flush()
    assert af.run_auto_finalize(db_session, now=NOW) == []


def test_downloaded_gone_part_does_not_block_known_total(db_session, tmp_path, book):
    """Expired on the web but already on disk — the book is finished
    (172 such books never shelved, found 2026-10-21)."""
    book.expected_total = 2
    book.episodes[0].availability_status = AvailabilityStatus.GONE
    db_session.flush()
    assert any("SHELVE" in r for r in af.run_auto_finalize(db_session, now=NOW))


def test_undated_parts_age_by_download_time(db_session, tmp_path, book):
    """Archive-walk episodes have no published_at; they never aged
    (128 finished works stuck, found 2026-10-21)."""
    for e in book.episodes:
        e.published_at = None
        for a in e.assets:
            a.first_seen_at = NOW - timedelta(days=30)
    db_session.flush()
    assert any("SHELVE" in r for r in af.run_auto_finalize(db_session, now=NOW))


def test_undated_fresh_download_still_waits(db_session, tmp_path, book):
    for e in book.episodes:
        e.published_at = None
        for a in e.assets:
            a.first_seen_at = NOW - timedelta(days=2)
    db_session.flush()
    assert af.run_auto_finalize(db_session, now=NOW) == []


def test_missing_narrator_reports_waiting(db_session, tmp_path, book):
    db_session.query(MetadataValue).filter_by(field="narrator").delete()
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any("WAITING-METADATA" in r for r in report)


def test_rapi_total_unmet_blocks_despite_quiet_period(db_session, tmp_path, book):
    """12-part book, 11 aired: the quiet-days fallback must not shelve it."""
    book.expected_total, book.expected_source = 3, "rapi"
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert not any("SHELVE" in r for r in report)
    assert any("WAITING-PARTS" in r and "2/3" in r for r in report), report


def test_non_rapi_total_keeps_quiet_period_fallback(db_session, tmp_path, book):
    book.expected_total, book.expected_source = 3, "user_offline"
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any("SHELVE" in r for r in report), report


def test_shelf_year_is_recording_year_from_publisher(db_session, tmp_path, book):
    """The nightly run must name the shelf like curated_destination: recording
    year from publisher, not the broadcast year (live: 'CRo 2026')."""
    book.title, book.year = "Jan Autor: Testkniha. Napinavy pribeh o testech", None
    record_value(db_session, "work", book.id, "publisher", "CRo2 2009",
                 FieldOrigin.ENRICHED, "t")
    db_session.flush()
    report = af.run_auto_finalize(db_session, now=NOW)
    dest = tmp_path / "fiction" / "Jan Autor [audio]" / "Jan Autor - Testkniha (cte Petr Cteci, CRo 2009)"
    assert dest.is_dir(), report


def test_same_title_already_on_shelf_waits(db_session, tmp_path, book):
    """A shelf folder with the same title in any naming style (user's
    'Autor Prijmeni - Titul (Ctenar)(1h47m)', an older 'Autor - Autor Titul…')
    means a copy is curated — the book waits for the user (live: Baruska
    shelved next to the user's own copy of the same reading)."""
    own = tmp_path / "fiction" / "Jan Autor [audio]" / "Autor Jan - Testkniha (Petr Cteci)(1h47m)"
    own.mkdir(parents=True); (own / "a.mp3").write_bytes(b"x")
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any(r.startswith("SAME-TITLE") for r in report), report
    assert not any(r.startswith("SHELVE") for r in report), report


def test_other_title_on_shelf_does_not_block(db_session, tmp_path, book):
    other = tmp_path / "fiction" / "Jan Autor [audio]" / "Jan Autor - (1999) Jina kniha"
    other.mkdir(parents=True); (other / "a.mp3").write_bytes(b"x")
    report = af.run_auto_finalize(db_session, now=NOW)
    assert any(r.startswith("SHELVE") for r in report), report


def test_librarian_queue_lists_waiting_books_largest_first(db_session, tmp_path, book):
    """The shelf-queue page shows the librarian's own verdicts (user:
    'I would have expected to find this in the system')."""
    for ep in book.episodes:
        ep.duration_ms = 60 * 60_000
    db_session.query(MetadataValue).filter_by(field="narrator").delete()
    db_session.flush()
    rows = af.librarian_queue(db_session, now=NOW)
    assert [(r["kind"], r["work_id"], r["minutes"]) for r in rows] == [
        ("WAITING-METADATA", book.id, 120)]
    assert rows[0]["author"] == "Jan Autor" and rows[0]["parts"] == 2
    assert not db_session.query(MetadataValue).filter_by(field="final_path").count()


def test_music_program_shelves_to_music_share():
    """Rockle is music: the music share, never eBOOKs (user 2026-10-12)."""
    root, layout = af.DESTINATIONS["rockle"]
    assert root.startswith("/media/music/") and layout == "collection"


# --- author trust gate: second sources (2026-11-07, Povídky klasiků) -------
# The work title drops the "Autor: " prefix, so the gate never saw the
# author in it; 21 short stories waited forever. The article url slug and
# the original episode title both carry the author.

@pytest.fixture()
def story(db_session, tmp_path, monkeypatch):
    monkeypatch.setitem(af.DESTINATIONS, "povidky klasiku",
                        (str(tmp_path / "fiction"), "book"))
    st = Station(code="CRo3", name="Vltava"); db_session.add(st); db_session.flush()
    prog = Program(station_id=st.id, name="Povídky klasiků")
    db_session.add(prog); db_session.flush()
    ser = Series(program_id=prog.id, name="Povídky klasiků")
    db_session.add(ser); db_session.flush()

    def make(author, title, ep_title, url):
        w = Work(series_id=ser.id, title=title, author=author, year=2025)
        db_session.add(w); db_session.flush()
        ep = Episode(work_id=w.id, title=ep_title, url=url,
                     published_at=NOW - timedelta(days=30))
        db_session.add(ep); db_session.flush()
        f = tmp_path / "lib" / f"{w.id}.mp3"
        f.parent.mkdir(exist_ok=True); f.write_bytes(b"x")
        db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                             status=AssetStatus.COMPLETE, file_path=str(f)))
        record_value(db_session, "episode", ep.id, "narrator", "Petr Cteci",
                     FieldOrigin.SCRAPED, "t")
        db_session.flush()
        return w
    return make


def _kinds(db_session, work):
    v = []
    af.run_auto_finalize(db_session, now=NOW, dry_run=True, verdicts=v)
    return [x["kind"] for x in v if x["work_id"] == work.id]


def test_author_confirmed_by_article_url_slug(db_session, story):
    w = story("Karel Ladislav Kukla", "Z raje do pekla. Bujny svet",
              "Z raje do pekla. Bujny svet",
              "https://vltava.rozhlas.cz/karel-ladislav-kukla-z-raje-do-pekla-bujny-svet-9600501")
    assert "AUTHOR-CHECK" not in _kinds(db_session, w)


def test_author_confirmed_by_episode_title_prefix(db_session, story):
    w = story("Karel Čapek", "Pastika, Organizujeme se, Tonda",
              "Karel Čapek: Pastika, Organizujeme se, Tonda",
              "https://www.mujrozhlas.cz/povidky-klasiku/pastika-organizujeme-se-tonda")
    assert "AUTHOR-CHECK" not in _kinds(db_session, w)


def test_byline_redaktor_still_waits(db_session, story):
    w = story("Petra Hynčíková", "Povídky Fráni Šrámka", "Povídky Fráni Šrámka",
              "https://vltava.rozhlas.cz/povidky-frani-sramka-9600502")
    assert "AUTHOR-CHECK" in _kinds(db_session, w)
