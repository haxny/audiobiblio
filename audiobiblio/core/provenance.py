"""Provenance resolution: which observed value wins for a metadata field.

Precedence (spec §2): MANUAL > ENRICHED > FILE > SCRAPED; ties -> newest observed_at.
Manual edits therefore can never be silently overwritten by automatic values.
"""
from __future__ import annotations

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
