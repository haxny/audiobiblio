# Changelog

All notable changes, findings, and deferrals, per delivery phase. Format loosely follows Keep a Changelog; versions bump at each phase merge. Deeper trails: `docs/decisions/` (why), `docs/dead-ends/` (what failed and must not be retried), `docs/journal/` (per-phase build journal with review findings), `docs/workflows.md` (living status of every workflow step), git history (per-task commits).

## [0.5.0] — 2026-07-07 — Phase 4: Sync & import (merged)

### Added
- `verify-files` CLI — DB↔disk reconciliation; 335 real dead asset paths marked MISSING with last-known-path preserved
- Generic-title guard (`is_generic_title`) — "Epizody pořadu" and friends can no longer become episode titles, tags, or filenames (user finding); 13 real rows cleaned
- Provenance ACTIVATED: ingest records SCRAPED observations; `PATCH /api/v1/episodes/{id}/metadata` records MANUAL edits; `has_manual` protection — crawls can never clobber user edits (author enrichment set-only-when-empty)
- Sync engine (`sync-tags` CLI): DB-resolved values projected onto file tags; FILE observations compete by rank; M4A-unreadable guard (exiftool absence can't destroy tags — NAS-safe); write failures reported
- Import scanner + `import_findings` table: dead-path recovery by basename, program-scoped fuzzy title matching, duplicate replace-via-trash; Import page with buckets and Accept/Accept+Move/Ignore
- Episode detail page: files with exists-badges, per-field provenance with origin badges, inline editing, audio preview player (Range/seeking works)
- Unified field→entity routing (`core.provenance.WORK_FIELDS`) shared by PATCH/views/sync/importer — final review caught three diverging copies (manual genre edits would have been invisible to sync)
- CHANGELOG + committed build journal (`docs/journal/`) + per-phase version bumps (this rule)

### Fixed
- Import accept now promotes FAILED/STALE assets to COMPLETE (gate finding on real data); records provenance under canonical field names post-move; generic titles never recorded from file tags; endpoint guards (400 no-episode, 409 stale-bucket)
- XSS in import findings table (self-caught in review)

### Findings (real data)
- Library scan: 752 findings (4 auto-matched by title, 748 awaiting user review on /import)
- Episode 25 carries a deliberate " (test)" manual title for the user to revert via the new inline edit
- Dockerfile lacks exiftool → sync silently skips M4A on NAS (guarded); add to image in Phase 6

## [0.4.0] — 2026-07-06 — Phase 3: Quality & upgrades (merged at 7bc5eeb)

### Added
- Approve/reject cascade: one click approves/rejects ALL of an episode's jobs (audio+metadata+webpage); Inbox shows one row per episode
- Media-info capture (bitrate/channels/sample-rate/duration) on every download + `backfill-mediainfo` CLI
- Trash module: nothing is ever deleted — dated `.trash/` folders with restore sidecars, 30-day purge (daily scheduler job)
- Upgrade detection on re-airs: `upgrade_candidates` + 5-branch `evaluate_reair`; duration diff >5 s = ad-suspect, NEVER auto-resolved (binding rule)
- Tag carry-over on file replacement (14 fields, old curated values win, "n/a" treated as empty)
- Upgrade staging & resolve API (replace/keep-old/dismiss) with crash-safe ordering (tags → trash → move → mediainfo → commit)
- Inbox "Upgrades" card with side-by-side durations and "possible ads" hint
- Dedupe page: 2-tier duplicate clusters (shared re-air URL, per-program fuzzy titles), dry-run merge preview, MANUAL-metadata protection
- UI density pass (user preference, binding): 1440 px container, tight tables/cards

### Fixed
- Plain track numbers (`16`, never `16 of 3` — totals lied on incomplete works) and episode titles always written when they differ from album (gate finding on real file)
- Merge safety (final review, empirically reproduced): child-row FK crash (AvailabilityLog/UpgradeCandidate) fixed with re-pointing + flush-before-trash; self-merge guard; HTMX JSON serialization bug that made Dedupe buttons dead
- Staging paths made absolute (CWD-relative paths broke resolves after server restart)

### Findings (real data)
- 301/359 audio assets had dead file paths → became Phase 4's first job
- Genre tags were never actually broken (deliberate freeform iTunes atom; earlier gate misread ©gen)

## [0.3.0] — 2026-07-03 — Phases 1+2: Foundation + Daily loop (merged at 014ded3, 5245d02)

### Phase 1 — Foundation
- First test suite (52 tests: characterization of diacritics/naming/dedupe + TDD core)
- Module restructure: core/sources/acquire/tags/dedupe/library/web with import-linter layer contract (violations parked & documented, never silenced)
- `metadata_values` table + provenance resolver (MANUAL > ENRICHED > FILE > SCRAPED)
- archive/ (83 files) mined into docs/dead-ends + docs/decisions, then deleted
- Fixed en route: broken `tag-fixer` entry point, missing `unidecode` dep, 12+ stale imports from the restructure (one would have crash-looped Docker)

### Phase 2 — Daily loop
- Per-target `approval_mode` (auto/review) threaded crawl→planning; legacy threshold kept for manual ingests
- infosoud-design UI shell (vanilla CSS, no Pico), Console, Inbox (approve/reject), Sources, Downloads with SSE live refresh
- Fixed en route: every HTML page 500'd on new Starlette (TemplateResponse API), crawl-now never persisted `last_crawled_at`
- Proven on real data: review path (approve→download→tagged file) and auto path (unattended download) both end-to-end

## [0.2.0] — pre-2026-07 — original codebase

See `docs/CHANGELOG-pre-redesign.md` for the pre-redesign history (tag fixer evolution, genre taxonomy, chapter-title preservation).
