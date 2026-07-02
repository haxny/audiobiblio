# dedupe — Content-aware duplicate detection for discovered episodes

**Layer:** Layer 4 of 5 (same tier as `sources` and `tags`). May import from `core` only. Called by `cli.ingest-program` and (planned) by `acquire.crawler`.
**Standalone use:** Library-only today; no dedicated CLI commands. Pass lists of `DiscoveredEpisode` objects directly to `dedupe_discovered()`.

## Responsibilities

- Deduplicates a list of newly discovered episodes against each other and against the existing DB, in three tiers: exact `ext_id` match, normalized URL match (with and without re-air suffixes), and fuzzy title match.
- Produces two outputs: the unique (non-duplicate) episodes, and a list of `DuplicateGroup` records describing what was dropped and why.
- Skips fuzzy matching for generic/placeholder titles (e.g., `"Epizody poradu"`) that would cause false positives.
- Accepts an optional `existing_episodes` list (DB Episode objects) so that already-ingested episodes are treated as canonical even if their record predates the current discovery run.
- Uses `core.urls.norm_url` and `core.urls.norm_url_strip_reair` for consistent URL comparison across the whole codebase (no local duplicates).

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `dedupe_discovered` | `(entries, existing_episodes=None, series_prefix=None) -> tuple[list, list[DuplicateGroup]]` | Content-aware dedup; returns `(unique, duplicate_groups)` |
| `DuplicateGroup` | dataclass | `canonical_url, canonical_title, duplicates: list[{url, title, reason}]` |

Deduplication tiers (applied in order):
1. `ext_id` — same UUID → same episode
2. `url_exact` — normalized URL exact match
3. `url_reair` — URL match after stripping trailing re-air numeric suffix (`-2941669`)
4. `title_fuzzy` — SequenceMatcher ratio > 0.9 on lowercased, diacritics-stripped, series-prefix-stripped titles

## Files

| File | Purpose |
|---|---|
| `matching.py` | `dedupe_discovered()`, `DuplicateGroup`, three-tier matching logic |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 3:** Quality scoring alongside deduplication: when two entries represent the same episode, score them by bitrate/container/duration to determine which is the keeper.
- **Phase 3:** Ad-suspect detection: episodes with the same content but differing durations beyond a tolerance threshold are flagged as an ad-suspect pair for manual review in the Inbox.
- **Phase 3:** Upgrade decisions: auto-replace when a higher-quality re-air is confirmed clean; carry over curated tags.
- **Phase 3:** Dedicated Dedupe page in the web UI showing duplicate clusters and a quality comparison tool.
