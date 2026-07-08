# Audiobiblio Workflows

Living document — kept current as the codebase evolves. Each step is marked:

- `[works today]` — implemented and in use on real data
- `[partial: <what>]` — partly done; the gap is described
- `[phase N]` — planned but not yet implemented

The design spec (frozen) is at [superpowers/specs/2026-07-02-audiobiblio-redesign-design.md](superpowers/specs/2026-07-02-audiobiblio-redesign-design.md).

---

> **Always-on monitoring** (scheduler running 24/7) requires the NAS deploy — see [deploy-nas.md](deploy-nas.md). Running `audiobiblio serve` only on a laptop means crawl jobs pause when the laptop is closed or offline.

## 4.1 Daily loop: monitor → review → download → library

Paste-episode-URL offers whole-program target: `/ingest` page classifies any pasted mujrozhlas URL; episode URLs surface a card offering to add the whole program as a CrawlTarget (review or auto mode) `[works today — Phase 5 Task 5]`

1. Scheduler runs due `CrawlTarget` rows (those where `next_crawl_at <= now`) `[works today]`
2. Source plugin discovers episodes from the target URL — four-layer merge: yt-dlp flat-playlist + AJAX pagination + HTML scrape + RAPI JSON `[works today]`
3. Dedupe matches new discoveries against DB: ext_id → normalized URL → re-air URL → fuzzy title `[works today]`
4. New episodes are ingested (`upsert_from_item`); `Asset` rows and `DownloadJob` rows created `[works today]`. Generic/placeholder titles (e.g. `"Epizody pořadu"` from mujrozhlas) are neutralised at ingest and in filename/tag paths via `is_generic_title()` — they fall back to `"Episode N"` `[works today — Phase 4 Task 2]`
5. Auto-download programs: per-target `approval_mode` overrides the threshold — `AUTO` queues immediately (`PENDING`), `REVIEW` holds in inbox (`APPROVAL`); `None` falls back to the threshold (first 3 per program need approval) `[works today]`
6. Review targets: jobs in `APPROVAL` status surface on the `/inbox` page (grouped by program, one row per episode); `POST /api/v1/jobs/{id}/approve` cascades to all APPROVAL siblings of the same episode → all move to `PENDING`; `POST /api/v1/jobs/{id}/reject` cascades → all move to `SKIPPED`; `approve-all`/`reject-all` bulk-update every APPROVAL job site-wide `[works today]`
7. Download worker (`run_pending_jobs`) runs yt-dlp for each `PENDING` job: AUDIO (`.m4a`, `--extract-audio --embed-thumbnail`), META_JSON (`--write-info-json --skip-download`), WEBPAGE (HTTP GET) `[works today]`
8. Post-download: write metadata tags to the audio file (`tags.tag_audio`) `[works today]`
9. Post-download: name per convention and move to library path (`move_to_library`) `[works today]`
10. Post-download: write `abs_metadata.json` for ABS; write `.nfo` sidecar when all episodes in a Work complete `[works today]`
11. Notify ABS via scan trigger `[partial: trigger_library_scan() exists and is called from postprocess_episode(); must be configured with ABS_URL + ABS_API_KEY env vars]`
12. Every step records state in the DB: `DownloadJob.status`, `Asset.status`, `Episode.availability_status`, `last_crawled_at` / `next_crawl_at` on the target `[works today]`

---

## 4.2 Upgrades (re-airs, better quality)

1. Already-owned episode found again during crawl → detected by re-air URL normalization; `EpisodeAlias` added and episode possibly revived if it was `GONE` `[works today]`
2. Evaluate upgrade: `dedupe.upgrades.evaluate_reair()` called after alias; creates `UpgradeCandidate` row when duration differs > 5 000 ms (ad-suspect) or candidate duration is unknown `[works today — spec §4.2 AD RULE]`
3. Stage download of candidate (user-triggered via Stage button): `POST /api/v1/upgrades/{id}/stage` downloads to staging dir, reads bitrate/duration into `note` `[phase 3]`; auto-staging based on quality comparison (higher bitrate / better container) `[phase 3+]`
4. Carry over curated tags from old file to new file `[phase 3]`
5. Old file moved to trash folder for 30 days `[phase 3]`
6. Duration differs beyond tolerance (~5 s) → flagged as **ad-suspect pair** in `upgrade_candidates` table; surfaces in Inbox showing durations `[works today — step 2 above]`
6a. Inbox `/inbox#upgrades` shows PENDING_REVIEW + STAGED pairs with duration diff, candidate link, and action buttons (Stage & compare → Replace/Keep old/Dismiss) `[works today: review pairs; auto-upgrade deliberately requires human resolve]`
7. Future: audio-fingerprint heuristics for auto-resolution of ad-suspect pairs `[phase 3+]`

---

## 4.3 Library import & unsorted inboxes

Scope order: go-forward pipeline first; legacy import is the second stage.

1. Scanner walks existing library folders and registered unsorted inbox folders — `scan_directory()` in `importer.py` `[works today]`
2. Reads tags + filenames via `parse_stem()` (NAMING_CONVENTION patterns 1–6); matches to DB episodes in four tiers: dead-path recovery (MISSING asset basename / last_known_path) → title fuzzy-match → duplicate check → unknown `[works today]`
3. Findings persisted in `import_findings` table with four buckets: **MATCHED** (single candidate; linked on accept), **DUPLICATE** (old file trashed via trash_fn then new linked), **UNKNOWN** (no match or multiple candidates), **CONFLICT** (reserved for future manual-resolution flows) `[works today — accept_finding() / ignore_finding()]`
4. Re-scannable: existing "new" findings updated with latest scan; resolved ("accepted"/"ignored") findings never re-opened `[works today]`
5. `accept_finding(move=True)` moves file to library-managed canonical path; `accept_finding(move=False)` links in-place `[works today]`
6. Config: `inbox_dirs: list[str]` in config.yaml / `AUDIOBIBLIO_INBOX_DIRS` env var (comma-separated) `[works today]`
7. Directory names on disk never modified without explicit approval; import links first, moves only on accept `[works today — standing rule enforced throughout]`
8. Review UI page for findings `[works today — Phase 4 Task 3: /import page with scan trigger, bucket tabs, accept/ignore per finding]`

---

## 4.4 Enrichment (databazeknih)

`[works today: meta_json + databazeknih on demand]`

1. Per Work: query databazeknih.cz for author, year, narrator, genres, cover, description — `search_book()` + `fetch_book()` in `sources/databazeknih.py`; rate-limited (1 req/2 s); UA `"audiobiblio/0.5 (personal audiobook manager)"` `[works today — Phase 5 Task 6]`
2. Cache results in `MetadataValue` rows with `ENRICHED` provenance — `enrich_work_from_dbk(session, work)` records all fields; also caches raw hit in `work.extra["dbk"]` `[works today — Phase 5 Task 6]`
3. Apply to tags per the rich-metadata tagging style (provenance rules: `ENRICHED` beats `SCRAPED`, `MANUAL` beats both) — `resolve_field()` in `core/provenance.py` `[works today]`
4. On demand via `POST /api/v1/works/{id}/enrich` (background task, own session pattern); "Re-enrich z databazeknih" button in episode detail metadata card `[works today — Phase 5 Task 6]`
5. Fuzzy title+author match (SequenceMatcher > 0.85); ambiguous hits are skipped with a reason logged `[works today — Phase 5 Task 6]`
6. Routing: year → work ORM (set-only-when-empty + MANUAL guard); description → work provenance-only; genre + narrator → episode-level per WORK_FIELDS design `[works today — Phase 5 Task 6]`
7. Runs automatically after download (on demand only for now; post-download hook deferred) `[partial: on-demand only; auto-trigger after download deferred to phase 5+]`

### 4.4.1 Meta_json enrichment [partial: meta_json live]

Reads back already-downloaded `.info.json` files to backfill episode titles/description/duration/episode_number that were unknown at ingest time (e.g. episodes ingested as "Episode 9" from a generic playlist URL).

- `enrich_episode_from_meta(session, episode)` in `library/enrich_meta.py` — per-field rules: title updated only when fallback-pattern (`^Episode \d+$`) or candidate is longer; `is_generic_title` guard; `has_manual` guard; provenance always recorded (`SCRAPED`, source="meta_json")
- CLI `enrich-from-meta [--limit N] [--dry-run]` — sweeps all episodes with a COMPLETE META_JSON asset, fallback-titled first
- Downloader hook — fires automatically after each successful META_JSON download (isolated try/except, never fails the job)
- `[works today — Phase 5 Task 1]`

---

## 4.5 Completeness & gap hunting

1. Scrape reference episode catalogs from Wikipedia episode tables and mluvenypanacek.cz into `CatalogEntry` rows `[works today — scrape_catalog() + upsert_catalog()]`
2. Gap report: compare catalog vs downloaded episodes, list missing ones per program `[works today — gap_report() + /catalog/{program_id} page]`
3. Set expected episode total per Work manually: `PATCH /api/v1/works/{id} {"expected_total": N}` — stored in `Work.expected_total` + `Work.expected_source="manual"` + MANUAL provenance row; 422 for non-positive, 404 for unknown work `[works today — Phase 5 Task 4]`
4. Work completeness: `work_completeness(session, work)` in `library/pipelines/completeness.py` — `have` = count of episodes with COMPLETE audio; `missing_numbers` computed when ≥80 % of episodes have distinct positive `episode_number` (sparse feeds get `None`) `[works today — Phase 5 Task 4]`
5. Incomplete works query: `incomplete_works(session, limit=100)` — works with `expected_total` set and `have < expected_total`, sorted by gap ascending (most nearly complete first) `[works today — Phase 5 Task 4]`
6. Gap view: `/gaps` page — dense table of incomplete works (title, program, have/expected, missing numbers when known, link to first episode); empty state shown when no gaps `[works today — Phase 5 Task 4]`
7. Console badge: `/` console shows "N gaps in expected totals" link to `/gaps` when any incomplete works exist `[works today — Phase 5 Task 4]`
8. Gap-fill priority: when a NEW episode is ingested into a work that has `expected_total` set and `have < expected_total`, the episode gets `priority = 10` and its download job reasons include `; gap-fill` (surfaced in Inbox) `[works today — Phase 5 Task 4]`
9. `WANTED` records for missing episodes — probed more often, sorted to top of Inbox when found `[phase 5]`
10. Cross-source hunting: every newly discovered episode on any source fuzzy-matched against the wanted list `[deferred: phase 5+]`
11. Gap report UI hunt button — "hunt now" targeted per-work cross-source search `[deferred: phase 5+]`

---

## 4.6 DB ↔ ID3 sync

1. Sync scan compares file tags to DB projections `[works today]`
2. Drift shows field-by-field diffs — per-field `FieldDiff(field, file_value, resolved_value, action)` in `SyncReport` `[works today]`
3. Resolution follows provenance rules (§2 of the design spec): `MANUAL > ENRICHED > FILE > SCRAPED` — `compute_resolved()` gathers `MetadataValue` rows for episode + work entities, resolves via `resolve_field()`; falls back to ORM values where no rows exist `[works today]`
4. FILE observations recorded automatically when file has a value not yet in the DB — FILE rank > SCRAPED, so a hand-edited file can promote its value above scraped-only DB entries `[works today]`
5. Manual edits (`MANUAL` origin) in DB always win and trigger "rewrite" action — file is updated when `--write` is used `[works today]`
6. All operations idempotent — second run after `--write` produces zero non-"none" diffs `[works today]`
7. CLI: `audiobiblio sync-tags [--episode-id N | --limit N] [--write]` — dry-run by default `[works today]`
8. Fields synced: title, author (artist/albumartist), narrator (performer), genre, description (→ comment tag), year (→ date tag) `[works today]`
