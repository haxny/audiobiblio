# 0002 — Metadata database is the source of truth; ID3 tags are projections

**Date:** 2026-07-02 · **Status:** accepted

Every metadata field is stored as observed values with provenance
(scraped/file/enriched/manual + timestamp) in `metadata_values`; the effective
value is computed by `core.provenance.resolve_field` (MANUAL > ENRICHED > FILE
> SCRAPED, ties -> newest). File tags are written FROM the DB, never trusted
over it. Consequence: conflicts are resolved once, in the DB, and cannot be
reintroduced by file operations. ABS gets metadata pushed; folder layout is an
export format, not a data model.
