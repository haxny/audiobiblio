"""Tests for ingest.upsert_from_item author enrichment and provenance logic."""
from audiobiblio.core.db.models import FieldOrigin, MetadataValue, UpgradeCandidate
from audiobiblio.library.pipelines.ingest import upsert_from_item


def test_author_enrichment_only_when_empty(db_session):
    """Author enrichment should only set author when empty, not churn on re-crawl.

    Scenario: existing episode with scraped author "Author A" is re-crawled with
    different author "Author B". The author field should NOT be overwritten because
    it was already set on the first crawl.

    We verify:
    1. Author remains "Author A" (not updated to "Author B")
    2. SCRAPED observation of "Author B" is recorded in provenance (for audit)
    """
    url = "https://example.cz/episode-1"

    # First ingest: create episode with author "Author A"
    ep1, work1 = upsert_from_item(
        db_session,
        url=url,
        item_title="Episode Title",
        series_name="Test Series",
        author="Author A",
        uploader="TestUploader",
        program_name="Test Program",
        source_url=None,
        ext_id=None,
        discovery_source="initial_scrape",
    )
    db_session.commit()

    # Re-crawl the same episode with a different author
    ep2, work2 = upsert_from_item(
        db_session,
        url=url,  # Same URL = should match via alias
        item_title="Episode Title",
        series_name="Test Series",
        author="Author B",  # Different author
        uploader="TestUploader",
        program_name="Test Program",
        source_url=None,
        ext_id=None,
        discovery_source="recheck_scrape",
    )
    db_session.commit()

    # Verify episode and work are the same (re-used, not created)
    assert ep2.id == ep1.id, "Should reuse existing episode"
    assert work2.id == work1.id, "Should reuse existing work"

    # Verify author was NOT updated (stayed as "Author A")
    assert work2.author == "Author A", "Author should not be overwritten on re-crawl"

    # Verify SCRAPED observations for both authors were recorded
    observations = db_session.query(MetadataValue).filter_by(
        entity_type="work",
        entity_id=work1.id,
        field="author",
        origin=FieldOrigin.SCRAPED,
    ).all()
    assert len(observations) == 2, f"Expected 2 observations, got {len(observations)}"

    author_a_obs = [o for o in observations if o.value == "Author A"]
    author_b_obs = [o for o in observations if o.value == "Author B"]

    assert author_a_obs, "Expected SCRAPED observation of 'Author A'"
    assert author_b_obs, "Expected SCRAPED observation of 'Author B'"
    assert author_a_obs[0].source == "initial_scrape"
    assert author_b_obs[0].source == "recheck_scrape"


def test_same_url_distinct_ext_ids_create_two_episodes(db_session):
    """Two items with same page URL but distinct ext_ids → two separate episodes."""
    PAGE = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/pribeh-sluzebnice"
    ep1, work1 = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087683", episode_number=1,
    )
    db_session.commit()
    ep2, work2 = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087684", episode_number=2,
    )
    db_session.commit()
    assert ep1.id != ep2.id, "distinct ext_ids → distinct episodes"
    assert work1.id == work2.id, "same work"


def test_reingest_same_ext_id_updates_not_duplicates(db_session):
    """Re-ingesting with same ext_id updates the existing episode, no duplicate created."""
    PAGE = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/pribeh-sluzebnice"
    ep1, _ = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087683", episode_number=1,
    )
    db_session.commit()
    ep2, _ = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice (updated)",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087683", episode_number=1,
    )
    db_session.commit()
    assert ep2.id == ep1.id, "same ext_id → same episode"


def test_parts_do_not_create_upgrade_candidates(db_session):
    """Two items with same URL but distinct ext_ids (no re-air suffix) → zero UpgradeCandidate rows.

    When two parts share the same URL but have distinct ext_ids, they create separate
    episodes. The ext_id guard ensures they do not collapse, so no re-air upgrade
    candidate should be created. This is distinct from re-air detection (which requires
    a 7+ digit numeric suffix).
    """
    PAGE = "https://www.mujrozhlas.cz/cetba-s-hvezdickou/pribeh-sluzebnice"

    # Ingest two parts with same URL, distinct ext_ids, no re-air suffix
    ep1, work1 = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087683", episode_number=1,
    )
    db_session.commit()

    ep2, work2 = upsert_from_item(
        db_session,
        url=PAGE, item_title="Příběh služebnice",
        series_name="Cetba", author=None, uploader="CRo",
        ext_id="12087684", episode_number=2,
    )
    db_session.commit()

    # Verify they are distinct episodes in same work
    assert ep1.id != ep2.id, "distinct ext_ids → distinct episodes"
    assert work1.id == work2.id, "same work"

    # Verify NO upgrade candidates were created (0 candidates expected)
    candidates = db_session.query(UpgradeCandidate).all()
    assert len(candidates) == 0, f"expected 0 upgrade candidates, got {len(candidates)}"


def test_ext_id_dedup_leaves_no_empty_work_shell(db_session):
    """A book ingested from mujrozhlas and later re-discovered via a station
    article (different program => different series/work identity) must NOT
    leave an empty duplicate Work behind (live case: Muklove a Slajsny,
    works/1405 shell next to works/1404)."""
    from audiobiblio.core.db.models import Work
    ep1, w1 = upsert_from_item(
        db_session, url="https://www.mujrozhlas.cz/porad/kniha-x",
        item_title="Kniha X", series_name="Porad A", author=None,
        uploader=None, program_name="Porad A",
        work_title="Kniha X", episode_number=1, ext_id="777001",
    )
    db_session.commit()
    ep2, w2 = upsert_from_item(
        db_session, url="https://wave.rozhlas.cz/kniha-x-1234567",
        item_title="Kniha X", series_name="Kniha X dlouhy clanek",
        author=None, uploader=None, program_name="Program B",
        work_title="Kniha X dlouhy clanek", episode_number=1, ext_id="777001",
    )
    db_session.commit()
    assert ep2.id == ep1.id
    assert w2.id == w1.id, "returned work must be the episode's real work"
    shells = [w for w in db_session.query(Work).all()
              if not w.episodes and w.id != w1.id]
    assert shells == [], f"empty work shells left behind: {[w.title for w in shells]}"


def test_title_prefix_author_beats_scraped_byline(db_session):
    """yt-dlp artist/creator on rozhlas articles is the ARTICLE byline
    (autor poradu), not the book author. The title prefix wins."""
    ep, work = upsert_from_item(
        db_session, url="https://wave.rozhlas.cz/kniha-y-7654321",
        item_title="Daniel Flasza: Muklove a Slajsny",
        series_name="Audioknihy", author="Michaela Sladká",
        uploader=None, program_name="Audioknihy",
        work_title="Daniel Flasza: Muklové a Šlajsny. Opravdový příběh",
        episode_number=1, ext_id="777100",
    )
    assert work.author == "Daniel Flasza"
