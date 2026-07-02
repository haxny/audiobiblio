# Audiobiblio Redesign — Design Spec

**Date:** 2026-07-02
**Status:** Approved
**Approach:** A — modular monolith evolved in place, with strict module boundaries ("B spirit") and a rebuilt frontend.

## 1. Goal

Turn the existing audiobiblio v0.2.0 codebase (~10k lines, working discovery/download/tagging/scheduler) into a usable, seamless system for semi-automated audiobook management on the Synology NAS:

monitor sources → review/approve → download → tag → dedupe → organize library → fill gaps.

Priority order (user-stated): **monitoring → downloading → tagging → managing → deduping.** A built-in player is a possible future module, explicitly out of scope for now; Audiobookshelf (ABS) remains the player.

## 2. Key Decisions

| Decision | Choice |
|---|---|
| Architecture | Modular monolith in this repo; one container; strict module boundaries enforced by import-lint |
| New-episode default | Per-target setting: each CrawlTarget chooses auto-download vs review-first |
| Upgrades | Auto-upgrade when clearly better; duration mismatch (>~5 s) is **never** auto-resolved (possible inserted ads) — goes to Inbox as ad-suspect pair; shorter-clean beats longer-with-ads |
| Tag carry-over | On upgrade/replace, curated tags from the old file always carry to the new file |
| Source of truth | The **metadata database**, with field-level provenance (scraped / enriched / file-read / manual + timestamp). ID3 tags on files are a synced projection of the DB |
| Conflict resolution | Manual edits outrank automatic values; newer enrichment outranks older scrape; manual-vs-manual → conflict queue in UI. Resolved once in DB, never silently reintroduced |
| Scope order | Go-forward pipeline first, then library import / unsorted-folder cleanup reusing the same dedupe+quality logic |
| ABS role | Player only. Audiobiblio manages, tags, organizes, pushes metadata, triggers scans. ABS integration is an optional module |
| UI | Server-rendered Jinja2 + HTMX + SSE. infosoud_web design language (vanilla CSS, blue gradient header #1a3a5c→#1e4d7b, white cards on #f4f6f9, status badges, optional Exo 2 theme). No JS build toolchain |
| Auth | None; LAN-only binding. Auth is a possible later module |
| DB | Existing SQLite + Alembic schema kept and extended, never restarted |
| History | archive/ (85 versioned files) mined into docs/dead-ends + docs/decisions, then deleted (git history retains bytes) |

## 3. Module Layout

```
audiobiblio/
├── core/       config, DB session, models, logging. Depends on nothing.
├── sources/    one plugin per source (mujrozhlas, rozhlas, sktorrent, cdwifi,
│               manual-URL). Plugin contract: list items at URL; fetch item metadata.
├── acquire/    CrawlTargets + scheduling, discovery runs, download queue,
│               yt-dlp / JDownloader execution.
├── tags/       existing tagging system (reader, writer, genre taxonomy, diacritics,
│               naming, role fixes, NFO). Mostly kept as-is.
├── dedupe/     duplicate matching (ext_id → normalized URL → fuzzy title),
│               quality scoring, ad-suspect detection, upgrade decisions,
│               tag carry-over.
├── library/    canonical ownership view: works/episodes ↔ files, import scanner
│               (legacy library + unsorted inbox folders), folder/naming export,
│               ABS metadata push (absorbs scripts/abs_*.py).
└── web/        FastAPI console + subpages. Depends on all; nothing depends on it.
```

Rules:
- Dependency direction: `core ← sources ← acquire`; `core ← tags`; `core ← dedupe`; `core ← library`; `web` on top. Enforced by a CI import-lint check.
- Every module: single responsibility, own CLI (`audiobiblio <module> <cmd>`), own docs page, usable standalone (e.g. `audiobiblio tags fix <folder>` needs no DB, no web server).
- Existing standalone scripts (sktorrent, cdwifi, abs_*) are absorbed into modules but keep CLI equivalents.

## 4. Core Workflows

### 4.1 Daily loop: monitor → review → download → library
Scheduler runs due CrawlTargets → source plugin lists episodes → dedupe matches against DB → new episodes become candidates. Auto targets queue downloads immediately; review targets go to the **Inbox** (approve/reject, single or bulk). Downloads via yt-dlp (JDownloader fallback) → post-process: write tags from source metadata + taxonomy rules, name per convention, move to library, notify ABS. Every step is a recorded state; the UI can always answer "where is this stuck and why".

### 4.2 Upgrades (re-airs, better quality)
Already-owned episode found again → compare: higher bitrate / better container → auto-download, carry over curated tags, replace; old file to trash folder for 30 days. Duration differs beyond tolerance → **ad-suspect pair** in Inbox showing durations, bitrates, silence profile. Future: audio-fingerprint heuristics may earn auto-resolution.

### 4.3 Library import & unsorted inboxes (second scope stage; delivery phase 4)
Scanner walks existing library folders **and registered unsorted inbox folders** (manual yt-dlp/JD dumps), reads tags + filenames, matches to known episodes/works. Review page, three buckets: **matched** (link; unsorted files additionally move into library), **duplicate** (pick keeper via quality rules), **unknown** (manual assign or leave alone). Standing rule respected: directory names on disk are never modified without explicit approval; import links first, moves only on approval.

### 4.4 Enrichment (databazeknih)
Per Work: query databazeknih.cz for author, year, narrator, series, cover, description. Cached in DB, applied to tags per the rich-metadata tagging style. Runs after download and on demand ("re-enrich").

### 4.5 Completeness & gap hunting
Every Work knows its expected episode list (source numbering, enrichment, or manual). Missing episodes are `WANTED` records:
- **Priority watch list** — probed more often; sort to top of Inbox when found.
- **Cross-source hunting** — every newly discovered episode on any source is fuzzy-matched against the wanted list (title + series + number); a gap from one channel can be filled by a re-air on another.
- **Gap report** — Library view of incomplete works ("9/12 — missing 4, 7, 11"), sortable by closeness to complete, with "hunt now" targeted search.

Builds on existing gaps.py + WATCH machinery, promoted to a core workflow.

### 4.6 DB ↔ ID3 sync
Sync scan compares file tags to DB projections; drift shows field-by-field diffs; resolution follows provenance rules (§2). All operations idempotent; manual edits are flagged and protected from automatic overwrite.

## 5. Web UI

Design: infosoud_web language (reference: ~/projects/rejstriky/infosoud_web/templates/base.html). Responsive; usable from a phone. HTMX partial updates + SSE live progress.

- **Console (home):** Inbox count (candidates, ad-suspect pairs, conflicts), active downloads with live progress, recent failures, per-source health, gaps counter, disk space.
- **Inbox:** approve/reject candidates, resolve upgrade pairs and tag conflicts; bulk actions.
- **Sources:** CrawlTarget CRUD, auto-vs-review switch, intervals, last-run results.
- **Downloads:** queue + history, retry/cancel, WATCH list.
- **Library:** DB-view browse/search of works/episodes, completeness badges, per-book detail (files, versions, metadata + provenance), gap report.
- **Tags:** web tag-fixer (current vs proposed side by side, apply), taxonomy editor.
- **Dedupe:** duplicate clusters, quality comparison, merge tool.
- **Import:** legacy/unsorted scanner with the three buckets.
- **System:** scheduler status, logs, job history, config.

## 6. Error Handling

- Per-item outcome records for every scheduled operation; failures surface as Console badges with error text + retry button, never only in logs.
- Circuit breakers per source: repeated failures pause a target and flag it. Rate limits kept (0.5 rps mujrozhlas).
- Transactional downloads: tag-write and library-move only after verified complete download; partial files never enter the library.

## 7. Testing

- Unit tests for pure logic: dedupe matching, quality scoring, naming, diacritics, tag rules.
- Fixture tests for source parsers from saved real HTML/JSON responses (site layout changes caught by re-recording fixtures).
- Web smoke test.
- `--dry-run` on every destructive operation; dev config pointing at a copy of a real library slice.

## 8. Documentation

```
docs/
├── README.md          entry point
├── modules/           one page per module (purpose, CLI, public API, standalone use)
├── decisions/         dated ADRs: "chose X over Y because Z"
├── dead-ends/         anti-library: "tried X, failed because Y, don't retry unless Z"
└── workflows.md       the core workflows, kept current
```

- archive/ mining is an early one-time task: extract lessons → dead-ends/decisions, verify nothing unique remains, delete directory.
- Existing docs (NAMING_CONVENTION.md, GENRE_TAXONOMY_README.md, TAG_ROLE_FIXES.md) move under docs/modules/tags/.
- Doc updates are part of done, not an afterthought.
- Duplicated helpers in live code (e.g. `_norm_url()` ×3) consolidated into core.

## 9. Delivery Phases (each ends usable on real data)

1. **Foundation** — module restructure, archive mining → docs, provenance fields in DB, tests for existing logic.
2. **Daily loop** — Inbox + per-target approval, new UI shell (infosoud design), Console, Sources, Downloads. *Daily use starts here.*
3. **Quality & upgrades** — quality scoring, ad-suspect detection, tag carry-over, Dedupe page.
4. **Sync & import** — DB↔ID3 sync with provenance, unsorted-folder scanner, Import page, conflict queue.
5. **Enrichment & gaps** — databazeknih client, completeness tracking, cross-source gap hunting.
6. **Polish** — ABS push absorbed, System page, mobile refinements.

## 10. Out of Scope (for now)

- Built-in listening player (future module; motivation recorded: ABS lacks cross-session settings memory, per-book positions with multiple books, offline play).
- Authentication (LAN-only).
- New sources beyond the current five (plugin contract makes additions cheap later).
