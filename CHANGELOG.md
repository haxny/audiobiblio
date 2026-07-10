# Changelog

All notable changes, findings, and deferrals, per delivery phase.

## [0.7.2] — 2026-07-10 — per-part identity (the real root cause)

### Fixed
- Multi-part books on a SINGLE page (mujrozhlas embeds all parts on one URL with identical titles) finally ingest as separate episodes: yt-dlp's per-part `id`/`episode_number`/`duration` were being DROPPED by `classify_probe` — identity now flows probe → dedupe (ext_id conflict guard on all tiers) → ingest (URL matches never merge differing ext_ids). Found by the user's live test with "Příběh služebnice" (12 parts → was 1 episode).
- Lock-in tests: parts never create false upgrade candidates; DB-episode ext_id conflicts never collapse.

## [0.7.1] — 2026-07-10 — hotfix + search (user-testing findings)

### Added
- Global search (header box): works/episodes/programs, diacritics-insensitive
- Curated fiction library mounted read-only into the container (`/media/fiction`) for import scanning
- `dedupe-jobs` CLI — cleans duplicate open download jobs

### Fixed (found live by the user on the NAS)
- Multi-part serialized books no longer collapse into one episode: mujrozhlas titles all parts identically, and tier-3 fuzzy dedupe swallowed them — fuzzy is now suppressed when both entries carry distinct URLs (cross-channel re-airs still caught by URL tiers + upgrade detection)
- `plan_downloads` no longer creates duplicate jobs on every crawl (skips assets with an open PENDING/APPROVAL/RUNNING/WATCH job)

## [0.7.0] — 2026-07-08 — Phase 6: Segmentation, ABS & System (merged)

### Added
- **Work segmentation engine** (`library/segmentation.py`): `propose_segmentation()` detects anthology/serialized/magazine modes from episode-title patterns (author_title conf 0.9, episode_title conf 0.7); handles 4-word false-positive anchor; `apply_segmentation()` is idempotent, dry-pure, and commits per-program (crash-safe)
- **`segment-works` CLI** (`--program-id N --dry-run / --apply`): dry-run prints full proposal table + action list without touching DB; apply re-parents episodes, creates per-story Works, deletes empty catch-all Works; MANUAL provenance rows block deletion
- **`/segmentation` page**: per-program proposal view, program selector, Apply/Discard buttons; unapplied programs left for user review
- **ABS module** (`library/abs.py`): push metadata (3 extensions) and sync (6 extensions); graceful when ABS is unconfigured
- **`/system` page**: scheduler job table (job ID + next-run timestamp), version badge, ABS configuration card (graceful unconfigured state with env-var hints), config table
- **`utcnow()` helper** in `core.time`: replaced project-wide `datetime.utcnow()` calls — pytest warnings 3 583 → 8; remaining: 7 Pydantic V2 class-based Config deprecations in `web/schemas.py` (future `ConfigDict` migration) + 1 test-side utcnow
- PATCH null-clearing for episode metadata fields
- Shared finalize JS extracted to static (deduped from two templates)
- Priority inbox: episodes with `priority > 0` surfaced first

### Fixed
- Rich markup crash in `segment-works --apply` output: `[/green]` close without open tag (cli.py); fixed to single well-formed print call
- Weak Pydantic assert in system router (carried minor from Task 5)

### Findings (real data)
- **Povídky klasiků (#101) applied at gate**: 12 per-story Works created (10 anthology conf 0.9 + 2 magazine conf 0.7); 12 episodes re-parented; old catch-all Work deleted; /gaps, /episodes, episode detail still render 200
- **4-word false-positive anchor**: Mezi kopci Zlínského kraje (#44) generated 5 anthology + 24 magazine proposals — anchor prevents short episode titles from becoming false work anchors; proposals left unapplied for user review on /segmentation
- **`/upgrades` 404** (not a regression — upgrades UI lives at `/inbox#upgrades`)
- New episodes in unapplied programs land in catch-all Work; /segmentation surfaces them as unassigned — auto-routing deferred

## [0.6.0] — 2026-07-08 — Phase 5: Enrichment, gaps & NAS prep (merged)

### Added
- Meta_json enrichment: the downloaded `.info.json` files are finally READ — 25 real episodes backfilled ("Episode 9" → "Karel Horký: Nad mrtvým netopýrem…"); future downloads self-enrich via a download hook
- NAS deployment kit: exiftool in the image, fixed container healthcheck (`/api/v1/health`), `docs/deploy-nas.md` incl. DB carry-over; review caught that the DB would have silently landed OUTSIDE the data volume (fixed via `XDG_DATA_HOME`)
- Source freshness: `target_state` helper, Console overdue badges, `crawl-status` CLI — the "sources are slipping" visibility (root cause solved by the NAS deploy: scheduler runs only while serve runs)
- Completeness: `Work.expected_total` (PATCH API + MANUAL provenance), `/gaps` report page, gap-fill priority on newly discovered episodes of incomplete works
- Paste-URL flow: paste an episode/series link → offer to add the whole program as a monitored source (with backfill crawl); `/episode/<uuid>` shapes guarded
- databazeknih enrichment: fixture-tested client (polite: 1 req/2 s, honest UA, never-raise), ENRICHED provenance, ambiguous-skip proven live (0.54 < 0.85 → zero writes)
- Finalize complete work: explicit, previewed move of a completed work's files into a per-work folder (flat-by-default stands)

### Fixed
- Sync engine gap (gate, real data): a GENERIC file title recorded as FILE observation outranked the enriched SCRAPED title — generic titles are now never recorded from files; the full circle (meta_json → DB → file tag) proven on disk
- Finalize preview/apply divergence (final review): shared-stem sidecars caused spurious `-2` renames on real runs only; parity now test-pinned (plan == applied)
- Paste-URL buttons were dead (quote collision in onclick) — DOM-wired listeners now
- enrich_meta could drop provenance-only rows (commit gated on ORM updates only)

### Findings (real data)
- **Works are program-level, not per-book** (ADR 0003): 9 works, all titled like programs — /gaps, Finalize and dbk matching operate at program granularity until "work segmentation" lands (next phase's first priority)
- Docker daemon absent on the dev Mac — first image build happens on the NAS (guide ready) Format loosely follows Keep a Changelog; versions bump at each phase merge. Deeper trails: `docs/decisions/` (why), `docs/dead-ends/` (what failed and must not be retried), `docs/journal/` (per-phase build journal with review findings), `docs/workflows.md` (living status of every workflow step), git history (per-task commits).

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
