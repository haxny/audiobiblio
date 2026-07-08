"""Timezone-safe UTC timestamp helper.

Returns a timezone-naive datetime in UTC, preserving the naive-UTC column
semantics of all DateTime columns in models.py (stored without tzinfo,
interpreted as UTC throughout).  Replaces the deprecated
``datetime.datetime.utcnow()``.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-naive datetime.

    Semantically equivalent to the deprecated ``datetime.utcnow()`` — the
    naive-UTC convention is intentional and matches the database schema.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
