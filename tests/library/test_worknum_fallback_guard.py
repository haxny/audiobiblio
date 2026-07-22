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
