"""Provenance resolution: which observed value wins for a metadata field.

Precedence (spec §2): MANUAL > ENRICHED > FILE > SCRAPED; ties -> newest observed_at.
Manual edits therefore can never be silently overwritten by automatic values.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from audiobiblio.core.db.models import FieldOrigin, MetadataValue

_ORIGIN_RANK: dict[FieldOrigin, int] = {
    FieldOrigin.SCRAPED: 1,
    FieldOrigin.FILE: 2,
    FieldOrigin.ENRICHED: 3,
    FieldOrigin.MANUAL: 4,
}


def resolve_field(candidates: Sequence[MetadataValue]) -> Optional[MetadataValue]:
    """Return the winning value among all observed values for one field."""
    if not candidates:
        return None
    return max(candidates, key=lambda v: (_ORIGIN_RANK[v.origin], v.observed_at))


def record_value(
    session,
    entity_type: str,
    entity_id: int,
    field: str,
    value: Optional[str],
    origin: FieldOrigin,
    source: str,
) -> MetadataValue:
    """Upsert one observed metadata value.

    Upsert key: (entity_type, entity_id, field, origin, source).
    Existing row → update value + observed_at=datetime.utcnow().
    No existing row → insert new row.
    No commit — caller's transaction owns the session.
    """
    row = (
        session.query(MetadataValue)
        .filter_by(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            origin=origin,
            source=source,
        )
        .first()
    )
    if row is not None:
        row.value = value
        row.observed_at = datetime.utcnow()
    else:
        row = MetadataValue(
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            value=value,
            origin=origin,
            source=source,
            observed_at=datetime.utcnow(),
        )
        session.add(row)
    return row
