# 0007 — Channel-level and series-level ingest

**What we tried**

Late versions of `download_sites_v*.py` (circa Aug 2025) added `channel_url` and
`series_url` fields, plus methods like `derive_series_url_from_episode()` and
`gather_series_from_channel()`. The intent was to ingest episodes at the
channel or series level — start with a channel listing page, discover series,
then discover episodes within each series.

**Why it failed**

Channel and series-level ingest caused the dual-source ingest problem: the same
episode could be discovered via a channel listing AND via a dedicated series page,
creating two diverging episode records with different metadata, URLs, and
acquisition histories. Merging and deduplication across such records proved
fragile. Per the critical rules: **ingest must use episode-level URLs only**.

**Don't retry unless**

A source guarantees stable, unique series-level IDs that map 1:1 to episodes.
Even then, extract episode URLs directly; do not ingest at a higher level.

**Where the code was**

`archive/download_sites_v*` series (deleted with `archive/` in commit a5e95cd).
