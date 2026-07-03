# Audiobiblio Workflows

Living document — kept current as the codebase evolves. Each step is marked:

- `[works today]` — implemented and in use on real data
- `[partial: <what>]` — partly done; the gap is described
- `[phase N]` — planned but not yet implemented

The design spec (frozen) is at [superpowers/specs/2026-07-02-audiobiblio-redesign-design.md](superpowers/specs/2026-07-02-audiobiblio-redesign-design.md).

---

## 4.1 Daily loop: monitor → review → download → library

1. Scheduler runs due `CrawlTarget` rows (those where `next_crawl_at <= now`) `[works today]`
2. Source plugin discovers episodes from the target URL — four-layer merge: yt-dlp flat-playlist + AJAX pagination + HTML scrape + RAPI JSON `[works today]`
3. Dedupe matches new discoveries against DB: ext_id → normalized URL → re-air URL → fuzzy title `[works today]`
4. New episodes are ingested (`upsert_from_item`); `Asset` rows and `DownloadJob` rows created `[works today]`
5. Auto-download programs: per-target `approval_mode` overrides the threshold — `AUTO` queues immediately (`PENDING`), `REVIEW` holds in inbox (`APPROVAL`); `None` falls back to the threshold (first 3 per program need approval) `[works today]`
6. Review targets: jobs in `APPROVAL` status surface on the `/inbox` page (grouped by program); `POST /api/v1/jobs/{id}/approve` or `approve-all` moves them to `PENDING`; `POST /api/v1/jobs/{id}/reject` or `reject-all` moves them to `SKIPPED` `[works today]`
7. Download worker (`run_pending_jobs`) runs yt-dlp for each `PENDING` job: AUDIO (`.m4a`, `--extract-audio --embed-thumbnail`), META_JSON (`--write-info-json --skip-download`), WEBPAGE (HTTP GET) `[works today]`
8. Post-download: write metadata tags to the audio file (`tags.tag_audio`) `[works today]`
9. Post-download: name per convention and move to library path (`move_to_library`) `[works today]`
10. Post-download: write `abs_metadata.json` for ABS; write `.nfo` sidecar when all episodes in a Work complete `[works today]`
11. Notify ABS via scan trigger `[partial: trigger_library_scan() exists and is called from postprocess_episode(); must be configured with ABS_URL + ABS_API_KEY env vars]`
12. Every step records state in the DB: `DownloadJob.status`, `Asset.status`, `Episode.availability_status`, `last_crawled_at` / `next_crawl_at` on the target `[works today]`

---

## 4.2 Upgrades (re-airs, better quality)

1. Already-owned episode found again during crawl → detected by re-air URL normalization; `EpisodeAlias` added and episode possibly revived if it was `GONE` `[works today]`
2. Compare quality: higher bitrate / better container → auto-download `[phase 3]`
3. Carry over curated tags from old file to new file `[phase 3]`
4. Old file moved to trash folder for 30 days `[phase 3]`
5. Duration differs beyond tolerance (~5 s) → flag as **ad-suspect pair** in Inbox showing durations, bitrates, silence profile `[phase 3]`
6. Future: audio-fingerprint heuristics for auto-resolution of ad-suspect pairs `[phase 3+]`

---

## 4.3 Library import & unsorted inboxes

Scope order: go-forward pipeline first; legacy import is the second stage. Full delivery in phase 4.

1. Scanner walks existing library folders and registered unsorted inbox folders `[phase 4]`
2. Reads tags + filenames, matches to known episodes/works in DB `[phase 4]`
3. Review page with three buckets: **matched** (link), **duplicate** (pick keeper via quality rules), **unknown** (manual assign or leave alone) `[phase 4]`
4. Directory names on disk are never modified without explicit approval; import links first, moves only on approval `[works today — standing rule enforced throughout]`

---

## 4.4 Enrichment (databazeknih)

Full delivery in phase 5.

1. Per Work: query databazeknih.cz for author, year, narrator, series, cover, description `[phase 5]`
2. Cache results in `MetadataValue` rows with `ENRICHED` provenance `[phase 5]`
3. Apply to tags per the rich-metadata tagging style (provenance rules: `ENRICHED` beats `SCRAPED`, `MANUAL` beats both) `[partial: provenance model exists in DB schema and resolve_field() is implemented; no databazeknih client yet]`
4. Runs after download and on demand ("re-enrich") `[phase 5]`

---

## 4.5 Completeness & gap hunting

1. Scrape reference episode catalogs from Wikipedia episode tables and mluvenypanacek.cz into `CatalogEntry` rows `[works today — scrape_catalog() + upsert_catalog()]`
2. Gap report: compare catalog vs downloaded episodes, list missing ones per program `[works today — gap_report() + /catalog/{program_id} page]`
3. `WANTED` records for missing episodes — probed more often, sorted to top of Inbox when found `[phase 5]`
4. Cross-source hunting: every newly discovered episode on any source fuzzy-matched against the wanted list `[phase 5]`
5. Gap report UI — Library view of incomplete works ("9/12 — missing 4, 7, 11"), sortable by closeness to complete, "hunt now" targeted search `[partial: /catalog/{program_id} shows a gap report; full Library view with completeness badges is phase 5]`

---

## 4.6 DB ↔ ID3 sync

Full delivery in phase 4.

1. Sync scan compares file tags to DB projections `[phase 4]`
2. Drift shows field-by-field diffs `[phase 4]`
3. Resolution follows provenance rules (§2 of the design spec): `MANUAL > ENRICHED > FILE > SCRAPED` `[partial: provenance model and resolve_field() exist; sync scan does not]`
4. Manual edits flagged and protected from automatic overwrite `[partial: provenance model protects MANUAL values in the DB; no sync-to-file workflow yet]`
5. All operations idempotent `[phase 4]`
