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

## Public interface — upgrades

| Name | Signature | Purpose |
|---|---|---|
| `evaluate_reair` | `(session, episode, candidate_url, candidate_duration_ms) -> UpgradeCandidate \| None` | Evaluate re-air URL; create upgrade candidate when warranted |
| `UpgradeCandidate` | ORM model | Stored in `upgrade_candidates` table; `(episode_id, candidate_url)` unique |
| `UpgradeStatus` | str-Enum | `PENDING_REVIEW`, `STAGED`, `REPLACED`, `KEPT_OLD`, `DISMISSED` |

### `evaluate_reair` decision branches (spec §4.2 AD RULE)

1. No COMPLETE AUDIO asset → `None` (normal re-download path handles it)
2. Both durations known and `abs(diff) <= 5 000 ms` → `None` (same content; alias only)
3. Both durations known and `abs(diff) > 5 000 ms` → `PENDING_REVIEW` candidate (ad-suspect; **NEVER auto-resolved**)
4. Candidate duration unknown → `PENDING_REVIEW` with note `"duration unknown"`
5. Existing `(episode_id, candidate_url)` row → return it unchanged (idempotent)

Owned duration comes from `episode.duration_ms` (set by mediainfo; `Asset` has no duration column).

## Files

| File | Purpose |
|---|---|
| `matching.py` | `dedupe_discovered()`, `DuplicateGroup`, three-tier matching logic |
| `upgrades.py` | `evaluate_reair()`, re-air upgrade candidate creation (spec §4.2) |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 3 (partial):** Ad-suspect detection via `evaluate_reair` — implemented. Auto-replace and tag carry-over remain planned.
- **Phase 3:** Auto-replace when a higher-quality re-air is confirmed clean; carry over curated tags.
- **Phase 3:** Dedicated Dedupe page in the web UI showing duplicate clusters and a quality comparison tool.
