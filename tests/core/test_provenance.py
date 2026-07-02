from datetime import datetime

from audiobiblio.core.db.models import FieldOrigin, MetadataValue
from audiobiblio.core.provenance import resolve_field


def _mv(origin: FieldOrigin, value: str, observed: str, source: str = "test") -> MetadataValue:
    return MetadataValue(
        entity_type="episode",
        entity_id=1,
        field="title",
        value=value,
        origin=origin,
        source=source,
        observed_at=datetime.fromisoformat(observed),
    )


def test_manual_beats_everything():
    winner = resolve_field([
        _mv(FieldOrigin.SCRAPED, "scraped title", "2026-07-01T00:00:00"),
        _mv(FieldOrigin.MANUAL, "my title", "2020-01-01T00:00:00"),
        _mv(FieldOrigin.ENRICHED, "dbk title", "2026-07-02T00:00:00"),
    ])
    assert winner.value == "my title"


def test_enriched_beats_file_and_scraped():
    winner = resolve_field([
        _mv(FieldOrigin.FILE, "file title", "2026-07-02T00:00:00"),
        _mv(FieldOrigin.ENRICHED, "dbk title", "2026-07-01T00:00:00"),
        _mv(FieldOrigin.SCRAPED, "scraped", "2026-07-02T00:00:00"),
    ])
    assert winner.value == "dbk title"


def test_same_origin_newest_wins():
    winner = resolve_field([
        _mv(FieldOrigin.SCRAPED, "old scrape", "2026-01-01T00:00:00"),
        _mv(FieldOrigin.SCRAPED, "new scrape", "2026-07-01T00:00:00", source="recrawl"),
    ])
    assert winner.value == "new scrape"


def test_empty_candidates_returns_none():
    assert resolve_field([]) is None
