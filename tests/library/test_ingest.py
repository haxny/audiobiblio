"""Tests for ingest.upsert_from_item author enrichment and provenance logic."""
from audiobiblio.core.db.models import FieldOrigin, MetadataValue
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
