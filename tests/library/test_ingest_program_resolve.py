"""Items ingested without a program name land in their real program, one
work per book — never in the catch-all "mujrozhlas" program.

Live case 2026-11-11: Otec Goriot (Radioseriál, Vltava) was downloaded on
2026-09-20 by the yt-dlp crawler, which passes no program name. It went to
the catch-all program 104 into one work "Radioseriál" with eight other
books (Velký Gatsby, Amadeus, Smetana…) and could never be shelved.
"""
from __future__ import annotations

from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, CrawlTargetKind, Program, Series, Station, Work,
)
from audiobiblio.library.pipelines.ingest import upsert_from_item
from audiobiblio.library.pipelines.program_resolve import (
    book_url, resolve_program, show_url,
)

SHOW = "https://www.mujrozhlas.cz/radioserial"
GORIOT = SHOW + "/honore-de-balzac-otec-goriot-slavny-francouzsky-roman"
GATSBY = SHOW + "/f-s-fitzgerald-velky-gatsby-ondrej-brousek"


def _vltava_radioserial(db):
    st = Station(code="CRo3", name="Vltava", website="https://vltava.rozhlas.cz")
    muj = Station(code="mujrozhlas", name="mujrozhlas.cz", website="https://www.mujrozhlas.cz")
    db.add_all([st, muj])
    db.flush()
    twin = Program(station_id=muj.id, name="Radioseriál", url=SHOW)
    real = Program(station_id=st.id, name="Radioseriál", url="https://vltava.rozhlas.cz/radioserial-8171393")
    db.add_all([twin, real])
    db.add(CrawlTarget(url="https://vltava.rozhlas.cz/radioserial-8171393", paired_url=SHOW,
                       kind=CrawlTargetKind.PROGRAM, name="Radioseriál (CRo3)",
                       approval_mode=ApprovalMode.AUTO))
    db.flush()
    return real


def _ytdlp_item(db, url, title, n, ext):
    # what crawler._ingest_episode passes: no program name, show as series
    return upsert_from_item(db, url=url, item_title=title, series_name="Radioseriál",
                            author=None, uploader=None, work_title="Radioseriál",
                            episode_number=n, ext_id=ext)


def test_show_and_book_urls():
    assert show_url(GORIOT) == SHOW
    assert book_url(GORIOT) == GORIOT
    assert show_url("https://www.mujrozhlas.cz/rapi/view/episode/x") is None
    assert show_url("https://vltava.rozhlas.cz/x-123") is None


def test_resolves_station_program_through_paired_target(db_session):
    real = _vltava_radioserial(db_session)
    assert resolve_program(db_session, GORIOT).id == real.id


def test_ytdlp_items_get_real_program_and_one_work_per_book(db_session):
    real = _vltava_radioserial(db_session)
    ep1, w1 = _ytdlp_item(db_session, GORIOT, "Honore de Balzac: Otec Goriot. Vautrin", 1, "10664475")
    ep2, w2 = _ytdlp_item(db_session, GORIOT, "Honore de Balzac: Otec Goriot. Anastazie a Delfina", 2, "10664478")
    ep3, w3 = _ytdlp_item(db_session, GATSBY, "Francis Scott Fitzgerald: Velky Gatsby", 1, "9573944")
    assert w1.series.program_id == real.id
    assert w1.id == w2.id != w3.id
    assert w1.title == "Honore de Balzac: Otec Goriot"
    assert w1.author == "Honore de Balzac"
    assert not db_session.query(Program).filter_by(name="mujrozhlas").count()


def test_part_joins_existing_station_page_work(db_session):
    real = _vltava_radioserial(db_session)
    series = Series(program_id=real.id, name="Radioseriál")
    db_session.add(series)
    db_session.flush()
    page_work = Work(series_id=series.id,
                     title="F. S. Fitzgerald: Velký Gatsby. Ondřej Brousek v titulní roli",
                     author="F. S. Fitzgerald")
    db_session.add(page_work)
    db_session.flush()
    _, w = _ytdlp_item(db_session, GATSBY, "Velky Gatsby", 1, "9573944")
    assert w.id == page_work.id


def test_unknown_show_still_falls_back(db_session):
    _, w = upsert_from_item(db_session, url="https://www.mujrozhlas.cz/neznamy-porad/dil",
                            item_title="Dil", series_name="Neznamy porad",
                            author=None, uploader=None)
    assert w.series.program.name == "mujrozhlas"


def test_rehome_splits_catch_all_work_into_books(db_session):
    """The history: the catch-all work 'Radioseriál' holds Goriot (2 parts)
    and Gatsby; Gatsby's part 1 already exists in the real program."""
    from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode
    from audiobiblio.library.pipelines.catchall_rehome import rehome
    real = _vltava_radioserial(db_session)
    muj = db_session.query(Station).filter_by(code="mujrozhlas").one()
    catch_all = Program(station_id=muj.id, name="mujrozhlas", url="https://www.mujrozhlas.cz")
    db_session.add(catch_all); db_session.flush()
    ser = Series(program_id=catch_all.id, name="Radioseriál"); db_session.add(ser); db_session.flush()
    lump = Work(series_id=ser.id, title="Radioseriál"); db_session.add(lump); db_session.flush()

    def ep(work, url, title, n, ext, audio=True):
        e = Episode(work_id=work.id, url=url, title=title, episode_number=n, ext_id=ext)
        db_session.add(e); db_session.flush()
        if audio:
            db_session.add(Asset(episode_id=e.id, type=AssetType.AUDIO,
                                 status=AssetStatus.COMPLETE, file_path=f"/x/{ext}.m4a"))
        return e
    g1 = ep(lump, GORIOT, "Honore de Balzac: Otec Goriot. Vautrin", 1, "g1")
    g2 = ep(lump, GORIOT, "Honore de Balzac: Otec Goriot. Anastazie", 2, "g2")
    gatsby_dup = ep(lump, GATSBY, "Francis Scott Fitzgerald: Velky Gatsby", 1, "f1")
    rser = Series(program_id=real.id, name="Radioseriál"); db_session.add(rser); db_session.flush()
    page = Work(series_id=rser.id, title="F. S. Fitzgerald: Velký Gatsby. Ondřej Brousek")
    db_session.add(page); db_session.flush()
    ep(page, "https://www.mujrozhlas.cz/rapi/x", "Velký Gatsby", 1, "uuid-f1")
    db_session.expire_all()

    stats, per_program, _ = rehome(db_session, catch_all, write=True)
    db_session.expire_all()
    g1, g2 = db_session.get(Episode, g1.id), db_session.get(Episode, g2.id)
    assert g1.work_id == g2.work_id != lump.id
    assert g1.work.series.program_id == real.id
    assert g1.work.title == "Honore de Balzac: Otec Goriot"
    assert db_session.get(Episode, gatsby_dup.id).work_id == lump.id   # duplicate left
    assert stats["duplicate_left"] == 1 and stats["episodes_moved"] == 2
