# Phase 6: Work Segmentation, ABS & System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Works become real BOOKS (ADR 0003 resolved): a segmentation engine proposes per-book/per-story works from title patterns + meta_json, a review page applies them safely; the abs_* scripts become a first-class module with System-page actions; a System page exposes scheduler/config/ABS; the polish backlog ships. Release 0.7.0.

**Architecture:** Segmentation is propose→review→apply (never automatic; per-program). Re-parenting is FK-safe (children hang off `episode_id`; only `episodes.work_id` moves) — work-level provenance/expected_total handling is explicit. Real title patterns (from the dev DB): Četba na pokračování = `"Jakob Wassermann: Kryštof Kolumbus…"` serialized with parts; Povídky klasiků = `"Karel Horký: Nad mrtvým netopýrem…"` one story per episode; SFT & Na nedělní vlně = magazine (no author prefix, standalone episodes). Note: the scout's "0 meta_json" was an enum-case artifact — META_JSON assets exist (uppercase names); the engine may use their `series` field as a secondary signal but must not depend on it.

**Tech Stack:** as before. No new external deps.

## Global Constraints

- Migrations (if any) chain from `2ad49dcfcbb6`. Segmentation itself needs NO schema change (Works exist; episodes.work_id moves).
- **User rules:** nothing automatic — segmentation applies per-program on explicit action with dry-run preview first; MANUAL provenance survives (work-level MANUAL rows like expected_total must be carried or surfaced, never silently orphaned); files untouched (this is DB-only); dense UI; no attribution in commits.
- Suite green (465+2), lint KEPT. Docs part of done. Branch `feature/phase6-segmentation-abs` off main. Port 8090 for live checks.
- ABS live calls: NONE in tests (fixtures/stubs only); the gate does a dry connectivity check ONLY if ABS_URL is configured, else documents skip (NAS not yet deployed).

---

### Task 1: Segmentation engine — propose (pure analysis)

**Files:** Create `audiobiblio/library/segmentation.py`; test `tests/library/test_segmentation.py`.

**Interfaces:**
- `ProposedWork` frozen dataclass: `title: str`, `author: str | None`, `episode_ids: tuple[int, ...]`, `signal: str` ("author_title_parts" | "author_title" | "episode_title"), `confidence: float`.
- `SegmentationProposal`: `program_id`, `mode: str` ("serialized" | "anthology" | "magazine"), `proposed: tuple[ProposedWork, ...]`, `unassigned: tuple[int, ...]`, `note: str`.
- `propose_segmentation(session, program) -> SegmentationProposal` — analyzes the program's episodes (via its catch-all work(s)):
  1. Parse each title with the author-prefix pattern `^([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^:]{2,60}?):\s+(.+)$` → (author, rest). Guard: the author part must look like a name (≤4 words, no digits) — otherwise treat as no-prefix.
  2. Strip part-markers from `rest` to get the book key: trailing `(\d+/\d+)`, `\(?\s*\d+\.\s*(díl|část)\)?`, `část \w+`, `- \d+$` … (use/extend the existing Czech ordinal knowledge in `tags/diacritics.py` `_CZECH_PARTS` — read it; share, don't duplicate).
  3. Cluster: same (author, book_key) with >1 episode and part markers → mode "serialized" contribution (one ProposedWork per book, parts ordered by part number then published_at); author-prefix without parts → "anthology" (one ProposedWork PER EPISODE: title=rest, author=author); no author-prefix majority → "magazine" (one ProposedWork per episode, title=episode title, author=None).
  4. Mode = majority signal across episodes; mixed programs allowed (each episode goes to its signal's bucket); confidence per ProposedWork (1.0 exact-part cluster; 0.9 author-prefix; 0.7 magazine).
  5. Episodes with generic/fallback titles (`is_generic_title` or `^Episode \d+$`) → `unassigned` (never proposed).
- Secondary signal: if an episode's META_JSON-derived provenance rows include a `series` value that differs from the program name, prefer it as book_key (check MetadataValue SCRAPED source="meta_json" field... series isn't recorded today — SKIP this; note as future signal. Do not read files in this engine.)

- [ ] TDD with the REAL patterns quoted in this plan's Architecture note (build fixtures from them verbatim; include: Wassermann-Kolumbus with 2 parts → one work 2 eps; Horký + Svoboda stories → 2 anthology works; SFT documentary titles → magazine per-episode; a generic "Episode 3" → unassigned; a false-positive guard: "Zlatý poklad republiky. Kam zmizely…" has no colon-author → magazine, and "Stalo se v zemi Nikoly Šuhaje…" similar). RED → GREEN.
- [ ] Suite + lint. Docs: library.md. Commit `feat: segmentation engine — propose per-book works from title patterns`

---

### Task 2: Segmentation apply — re-parent safely

**Files:** Extend `segmentation.py`; modify `audiobiblio/cli.py` (`segment-works` command); test extend.

**Interfaces:**
- `apply_segmentation(session, proposal, dry_run: bool = True, only_titles: set[str] | None = None) -> list[str]` (action list):
  - For each ProposedWork: find-or-create Work (unique (series_id, title) — reuse the episode's existing series_id; set author); UPDATE episodes.work_id for its episode_ids; `session.flush()` per work.
  - Work-level provenance carry: MetadataValue rows with entity_type="work" on the OLD work (e.g. author SCRAPED, expected_total MANUAL) — expected_total does NOT transfer (it described the program lump; add to action list "expected_total X left on old work — review"); author rows re-recorded on the new work when the proposal has an author (record_value SCRAPED source="segmentation").
  - Old catch-all work: if left with 0 episodes → delete ONLY when it has no MANUAL metadata_values rows; else keep + action note. (Never delete works still holding episodes.)
  - Dry-run: pure (no flush/commit); returns the same action list. Commit at the end of a real run.
- CLI: `segment-works [--program-id N] [--dry-run/--apply]` (dry default) — prints proposal table + actions.

- [ ] TDD: re-parent moves episodes (children untouched — assert an Asset still reachable via episode), find-or-create idempotence (re-apply → no dupes), empty-work deletion rules (MANUAL row blocks deletion), expected_total note emitted, dry-run purity, only_titles filter. RED → GREEN.
- [ ] Suite + lint. Docs: library.md + workflows §4.5 granularity note updated (segmentation available). Commit `feat: apply segmentation — safe episode re-parenting with provenance rules`

---

### Task 3: Segmentation review page

**Files:** views.py route `/segmentation` (+nav after Gaps); template `segmentation.html`; new thin router `web/routers/segmentation.py` (`GET /api/v1/segmentation/{program_id}` → proposal JSON; `POST /api/v1/segmentation/{program_id}/apply` {"dry_run": bool, "titles": [..]|null} → action list; include in app); tests router-level.

Page: program selector (the 4+ programs), proposal table per mode (dense): proposed work title, author, #episodes, signal badge, confidence; checkboxes per proposed work; [Náhled] (dry actions into details) / [Aplikovat vybrané] (confirm → apply with titles filter → reload). Raw-fetch pattern (data-returning). Unassigned list shown muted.

- [ ] TDD router (proposal shape, apply happy+dry, 404 program). Route census. Suite + lint. Docs: web.md. Commit `feat: segmentation review page — propose, preview, apply per program`

---

### Task 4: ABS module absorption

**Files:** Create `audiobiblio/library/abs.py` (absorb the reusable cores of scripts/abs_push_metadata.py + abs_sync_metadata.py: `push_item_metadata`, `sync_bad_titles`, both built on a small `AbsClient` class wrapping the existing abs_client.py functions + PATCH /api/items/{id}/media; config: add `abs_url: str = ""`, `abs_api_key: str = ""` to Config (env AUDIOBIBLIO_ABS_URL/ABS_API_KEY; keep legacy ABS_URL/ABS_API_KEY envs as fallback — the scripts used those); rate limit 10 rps reuse). Scripts become thin wrappers importing the module (keep their CLIs working). Tests with a stubbed HTTP layer (requests-mock style via monkeypatch — no live calls).

- [ ] TDD: client auth header, patch-building parity with the script logic (port 2-3 representative cases from abs_sync's needs_fix/tag-extraction), config fallback order. Scripts still import-clean. Suite + lint (library imports core ✓). Docs: library.md + scripts note. Commit `feat: absorb ABS metadata scripts into library.abs module`

---

### Task 5: System page

**Files:** views.py `/system` route (+nav last); `system.html`; extend `web/routers/system.py` (GET /api/v1/system/scheduler → jobs from `request.app.state.scheduler.get_jobs()` with id/next_run_time; existing /stats + abs-scan reused); tests.

Page (dense cards): version (pyproject via importlib.metadata); scheduler jobs table (id, next run) + running badge; stats block; ABS card (configured? URL shown redacted-key; [Spustit ABS scan] button → existing POST /api/v1/system/abs-scan via apiJson); config summary (library_dir, download_dir, inbox_dirs, trash_retention_days — read-only); links to /logs.

- [ ] TDD: scheduler endpoint (stub scheduler with fake get_jobs), version presence, page context. Census. Suite + lint. Docs: web.md. Commit `feat: system page — scheduler, stats, ABS actions, config summary`

---

### Task 6: Polish sweep (backlog burn-down)

**Files:** as needed.
1. `core/time.py`: `utcnow()` returning timezone-naive utc (wraps `datetime.now(timezone.utc).replace(tzinfo=None)` — preserves column semantics) — mechanical replace of ALL `datetime.utcnow()` across audiobiblio/ (models defaults incl.); tests keep passing untouched (values equivalent).
2. works PATCH: allow `{"expected_total": null}` → clears (sets None) + removes/updates the MANUAL provenance row (record with value None); 422 stays for ≤0 ints.
3. Finalize JS deduplicated into `static/audiobiblio.js` (both templates include).
4. Inbox ordering: `priority DESC, id ASC` (gap-fill first) — one-line + test.
5. LIMIT-20 overdue-counter comment (views).
6. JSDoc orphan fix in audiobiblio.js.

- [ ] TDD where behavior changes (2,4); mechanical elsewhere. Suite + lint. Docs touched where user-visible (web.md PATCH null). Commit `chore: polish sweep — tz-safe utcnow, expected_total clearing, shared finalize JS, priority inbox`

---

### Task 7: Phase 6 gate + release prep

- [ ] Suite + lint + heads (no new migration expected — confirm) + census (incl /segmentation /system).
- [ ] **Real-data segmentation (the point of the phase):** `segment-works --dry-run` for ALL programs — quote full proposals; then APPLY for "Povídky klasiků" (clearest anthology case) via CLI; verify: works table now has per-story rows (quote 3), episodes re-parented, /gaps and episode detail still render, dedupe page unaffected. Leave other programs unapplied for the user's review on /segmentation.
- [ ] System page live check (scheduler jobs visible); ABS card shows unconfigured state gracefully (no ABS_URL locally).
- [ ] CHANGELOG 0.7.0 entry + version bump + journal snapshot (release commit happens at merge per ground rules).
- [ ] Docs sweep + full report.

---

## Deferred (recorded)

- meta_json `series` as a segmentation signal (needs recording series into provenance at enrich time first) — next iteration.
- Auth for the web UI; cross-source hunting engine; ABS live e2e (post-NAS-deploy).
- Import/apply of segmentation to episodes arriving AFTER a program was segmented (new episodes land in the catch-all; the review page shows them as unassigned — note in docs; auto-routing new arrivals = future).
