"""The (work_id, episode_number) fallback in upsert_from_item must never
clobber an episode whose ext_id differs from the incoming item's.

Found live (0.7.4 on NAS): all books of one program share a catch-all Work,
so a crawl of "Svět podle Garpa" part 1 matched "Příběh služebnice" part 1
by (work, number) and overwrote its title AND url — for every part number
both books had. ext_id is the identity; number within a catch-all work is not.

Companion rule: when a match IS made via ext_id (the strongest identity),
the episode's url follows the incoming page url — pages move, media ids don't.
This also lets a re-ingest self-heal previously clobbered urls.
"""
from __future__ import annotations

from audiobiblio.core.db.models import Episode
from audiobiblio.library.pipelines.ingest import upsert_from_item

BOOK_A_URL = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/margaret-attwoodova-pribeh-sluzebnice"
BOOK_B_URL = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/john-irving-svet-podle-garpa"


def _ingest_part(session, *, url, title, number, ext_id):
    return upsert_from_item(
        session,
        url=url,
        item_title=title,
        series_name="Četba s hvězdičkou",
        author=None,
        uploader="mujrozhlas",
        work_title="Četba s hvězdičkou",
        episode_number=number,
        ext_id=ext_id,
    )


def test_worknum_fallback_never_clobbers_conflicting_ext_id(db_session):
    ep_a, work = _ingest_part(
        db_session, url=BOOK_A_URL,
        title="Margaret Atwoodová: Příběh služebnice", number=1, ext_id="111")

    ep_b, work_b = _ingest_part(
        db_session, url=BOOK_B_URL,
        title="John Irving: Svět podle Garpa", number=1, ext_id="222")

    assert work_b.id == work.id, "both books land in the same catch-all work"
    assert ep_b.id != ep_a.id, "conflicting ext_ids must create a NEW episode"

    fresh_a = db_session.get(Episode, ep_a.id)
    assert fresh_a.title == "Margaret Atwoodová: Příběh služebnice"
    assert fresh_a.url == BOOK_A_URL
    assert fresh_a.ext_id == "111"


def test_worknum_fallback_still_matches_when_no_ext_conflict(db_session):
    ep1, _ = _ingest_part(
        db_session, url=BOOK_A_URL, title="Kniha, díl 1", number=1, ext_id=None)
    ep2, _ = _ingest_part(
        db_session, url=BOOK_A_URL, title="Kniha, díl 1 (repríza)", number=1, ext_id="333")

    assert ep2.id == ep1.id, "no ext_id on the existing row → number match holds"
    assert ep2.ext_id == "333", "ext_id backfilled on match"


def test_ext_id_match_realigns_url(db_session):
    ep, _ = _ingest_part(
        db_session, url=BOOK_B_URL,
        title="Margaret Atwoodová: Příběh služebnice", number=1, ext_id="111")
    assert ep.url == BOOK_B_URL  # clobbered/stale url

    ep2, _ = _ingest_part(
        db_session, url=BOOK_A_URL,
        title="Margaret Atwoodová: Příběh služebnice", number=1, ext_id="111")

    assert ep2.id == ep.id
    assert ep2.url == BOOK_A_URL, "url follows the ext_id identity"


class TestProgramNameNormalization:
    """'Stopy, fakta, tajemství...' vs 'Stopy, fakta, tajemstvi' vs the
    diacritic form are ONE program — exact-string identity created the
    same program three times under one station."""

    def test_name_variants_reuse_one_program(self, db_session):
        from audiobiblio.core.db.models import Program
        variants = [
            "Stopy, fakta, tajemství",
            "Stopy, fakta, tajemstvi",
            "Stopy, fakta, tajemství...",
            "STOPY, FAKTA, TAJEMSTVÍ",
        ]
        for i, name in enumerate(variants):
            upsert_from_item(
                db_session,
                url=f"https://dvojka.rozhlas.cz/dil-{i}-123456{i}",
                item_title=f"Díl {i}",
                series_name=name,
                author=None,
                uploader=None,
                program_name=name,
                source_url="https://dvojka.rozhlas.cz/x",
                work_title=f"Díl {i}",
                episode_number=1,
                ext_id=f"norm-{i}",
            )
        progs = db_session.query(Program).all()
        assert len(progs) == 1, [p.name for p in progs]


class TestCuratedLayouts:
    """Finalize-to-curated naming (user conventions, all unidecoded)."""

    def _work(self, db_session, author="Johan Theorin", narrator="Gustav Hasek"):
        from datetime import datetime
        from audiobiblio.core.db.models import Episode, Program, Series, Station, Work
        st = Station(code="CRo2", name="Dvojka"); db_session.add(st); db_session.flush()
        prog = Program(station_id=st.id, name="Cetba"); db_session.add(prog); db_session.flush()
        ser = Series(program_id=prog.id, name="Cetba"); db_session.add(ser); db_session.flush()
        w = Work(series_id=ser.id, title="Mlhy Ölandu", author=author, year=2007)
        db_session.add(w); db_session.flush()
        ep = Episode(work_id=w.id, title="d1", episode_number=1,
                     published_at=datetime(2025, 3, 1))
        db_session.add(ep); db_session.flush()
        return w, ep

    def test_book_layout(self, db_session, tmp_path):
        from audiobiblio.library.pipelines.finalize import derive_curated_book_dir
        w, ep = self._work(db_session)
        d = derive_curated_book_dir(w, ep, tmp_path, "Gustav Hašek", "CRo2")
        assert d == tmp_path / "Johan Theorin [audio]" / \
            "Johan Theorin - (2007) Mlhy Olandu (cte Gustav Hasek, CRo2 2025)"

    def test_book_layout_refuses_missing_narrator(self, db_session, tmp_path):
        from audiobiblio.library.pipelines.finalize import derive_curated_book_dir
        w, ep = self._work(db_session)
        assert derive_curated_book_dir(w, ep, tmp_path, None, "CRo2") is None

    def test_collection_layout(self, tmp_path):
        from audiobiblio.library.pipelines.finalize import derive_curated_collection_dir
        d = derive_curated_collection_dir(tmp_path, "Stopy, fakta, tajemství (CRo2)")
        assert d == tmp_path / "Stopy, fakta, tajemstvi (CRo2)"

    def test_finalize_dest_override(self, db_session, tmp_path):
        from audiobiblio.core.db.models import Asset, AssetStatus, AssetType
        from audiobiblio.library.pipelines.finalize import finalize_work
        w, ep = self._work(db_session)
        src = tmp_path / "lib" / "01.mp3"
        src.parent.mkdir(); src.write_bytes(b"x" * 16)
        db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                             status=AssetStatus.COMPLETE, file_path=str(src)))
        db_session.flush()
        dest = tmp_path / "fiction" / "Author [audio]" / "kniha"
        r = finalize_work(db_session, w, tmp_path / "lib",
                          dry_run=False, dest_dir_override=dest)
        assert r.moved == 1
        assert (dest / "01.mp3").exists()
        assert not src.exists()
