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
| `GENERIC_TITLES` | `frozenset[str]` | Normalised placeholder titles (`"epizody poradu"`, `"episodes"`, `"vsechny dily"`, `"all episodes"`) |
| `is_generic_title` | `(title: str) -> bool` | True if title (after diacritics-strip + lower + whitespace-collapse) is in GENERIC_TITLES |
| `dedupe_discovered` | `(entries, existing_episodes=None, series_prefix=None) -> tuple[list, list[DuplicateGroup]]` | Content-aware dedup; returns `(unique, duplicate_groups)` |
| `DuplicateGroup` | dataclass | `canonical_url, canonical_title, duplicates: list[{url, title, reason}]` |

Deduplication tiers (applied in order):
1. `ext_id` — same UUID → same episode
2. `url_exact` — normalized URL exact match
3. `url_reair` — URL match after stripping trailing re-air numeric suffix (`-2941669`)
4. `title_fuzzy` — SequenceMatcher ratio > 0.9 on lowercased, diacritics-stripped, series-prefix-stripped titles; suppressed when both entries carry distinct non-empty stripped URLs (multi-part serialized books share titles)

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

## Public interface — clusters (Phase 3)

| Name | Signature | Purpose |
|---|---|---|
| `find_duplicate_clusters` | `(session, limit=200) -> list[Cluster]` | Surface Tier-A and Tier-B duplicate pairs from the library |
| `merge_episodes` | `(session, canonical_id, duplicate_id, library_dir, dry_run=True, trash_fn=None) -> list[str]` | Merge duplicate into canonical; returns action list |
| `Cluster` | TypedDict | `{key: str, reason: "same_stripped_url"\|"fuzzy_title_same_program", episodes: list[Episode]}` |
| `ManualMetadataProtectionError(ValueError)` | exception | Raised if duplicate carries MANUAL MetadataValue rows; router maps to HTTP 409 |

### Cluster tiers

| Tier | Reason | Condition |
|---|---|---|
| A | `same_stripped_url` | COMPLETE-audio episodes sharing `norm_url_strip_reair(url)` |
| B | `fuzzy_title_same_program` | Episodes in same program with SequenceMatcher ratio > 0.9; generic titles excluded; programs > 300 eps skipped (logged) |

### Layer-clean trash injection

`merge_episodes` must not import from `library` (dedupe is below library in the layer hierarchy).  File deletion is delegated via the `trash_fn: Callable[[Path], Path]` parameter.  The **web router** (`web/routers/dedupe.py`), which is in the top layer and may import both `dedupe` and `library`, injects `move_to_trash` as the callable.

### Merge semantics

1. Refuse if duplicate has MANUAL MetadataValue rows (`ManualMetadataProtectionError`).
2. Add duplicate's URL as `EpisodeAlias` on canonical.
3. Call `trash_fn(audio_file_path)` to move the duplicate's audio file to trash (never deleted directly).
4. Delete duplicate's Asset and DownloadJob rows.
5. Delete the duplicate Episode row.
6. `dry_run=True` (default): compute and return the action list only — no DB or filesystem changes.

## Files

| File | Purpose |
|---|---|
| `matching.py` | `dedupe_discovered()`, `DuplicateGroup`, three-tier matching logic |
| `upgrades.py` | `evaluate_reair()`, re-air upgrade candidate creation (spec §4.2) |
| `clusters.py` | `find_duplicate_clusters()`, `merge_episodes()`, `ManualMetadataProtectionError` |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 3 (partial):** Ad-suspect detection via `evaluate_reair` — implemented. Auto-replace and tag carry-over remain planned.
- **Phase 3 (done):** Dedupe page — duplicate clusters (`find_duplicate_clusters`) and dry-run merge tool (`merge_episodes`) with MANUAL-metadata protection guard.
- **Phase 3:** Auto-replace when a higher-quality re-air is confirmed clean; carry over curated tags.
