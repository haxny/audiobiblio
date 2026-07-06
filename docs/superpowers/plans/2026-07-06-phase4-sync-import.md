# Phase 4: Sync & Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The metadata database becomes the *actually used* source of truth: provenance rows get written (scraped/file/manual), file tags become synced projections, the disk gets reconciled with the DB (301 dead paths from the Phase 3 gate), unsorted folders get scanned into review buckets, and every episode gets a detail page with files, provenance, inline editing, and a preview player.

**Architecture:** Spec §2, §4.3, §4.6. Provenance writers activate `metadata_values` (empty until now — zero writers exist). The sync engine computes resolved values via the existing `core.provenance.resolve_field` and treats file tags as projections. The import scanner persists findings in a new `import_findings` table consumed by an Import page. Prior art: `scripts/abs_generate_metadata.py` (tag>text>folder precedence) and `tags/carryover.py` (reader→writer mapping).

**Tech Stack:** Python ≥3.10, mutagen (+ check reader.py's exiftool dependency for m4a), SQLAlchemy/Alembic, FastAPI/Jinja2/HTMX, pytest, uv.

## Global Constraints

- New migrations chain from head `8e3696d70603`, single chain, drift noise stripped + documented.
- **User rules (binding):** files NEVER deleted (trash only); directory names on disk never modified without explicit approval — the import scanner LINKS first, MOVES only inbox files on explicit user Accept; manual edits (origin MANUAL) always outrank automatic values and are never overwritten by sync; plain track numbers; dense UI for all new pages.
- Provenance precedence (spec §2): MANUAL > ENRICHED > FILE > SCRAPED, ties → newest `observed_at`. SAEnum stores NAMES ("MANUAL") — raw SQL must use names, ORM enums elsewhere.
- Suite green (`uv run pytest -q`, currently 173), `uv run lint-imports` KEPT (6 documented ignore_imports entries — new cross-layer needs follow the same parked style + decisions-doc line).
- Docs are part of done. Commits `<type>: <description>`, no AI attribution. New branch `feature/phase4-sync-import` off main.
- The user actively uses the app: any live verification server runs on **port 8090**; real-DB mutations only where a task explicitly says so, dry-run first.

---

### Task 1: Dead-path reconciliation — `verify-files`

The Phase 3 gate found 301/359 COMPLETE audio assets pointing at nonexistent files (March-era folder layout since reorganized). The DB must learn the truth.

**Files:** Create `audiobiblio/library/filecheck.py`; modify `audiobiblio/cli.py` (command `verify-files`); test `tests/library/test_filecheck.py`.

**Interfaces:**
- `verify_asset_paths(session, limit: int | None = None, fix: bool = False) -> FileCheckReport` — frozen dataclass `FileCheckReport(checked: int, ok: int, missing: list[tuple[int, str]])` (asset id, path). For each COMPLETE asset with a file_path: `Path(file_path).expanduser().exists()`. With `fix=True`: set `status=AssetStatus.MISSING` and stash the dead path in `asset.extra["last_known_path"]` (merge into existing extra dict, don't clobber), keep `file_path` untouched (it documents where the file was). No commits in dry mode.
- CLI: `audiobiblio verify-files [--limit N] [--fix]` (default dry-run prints table + counts; `--fix` applies). Follow the backfill-mediainfo command's typer style.

- [ ] TDD: tests — existing file stays COMPLETE; missing file reported (dry: no DB change); `fix=True` sets MISSING + last_known_path preserved + file_path untouched; extra-dict merge doesn't clobber existing keys; limit respected. RED → implement → GREEN.
- [ ] Full suite + lint. Docs: `docs/modules/library.md`.
- [ ] Real-data: `uv run audiobiblio verify-files` (dry) against the dev DB — quote counts (expect ~301 missing). Do NOT --fix yet (the gate does it after user-facing review of the report).
- [ ] Commit `feat: verify-files — reconcile asset paths with disk`

---

### Task 2: Generic-title guard at ingest + filename protection

"Epizody poradu" became an episode title AND a filename (user finding). `_GENERIC_TITLES` exists in `dedupe/matching.py:24` but is consulted only for fuzzy dedup, never at ingest (`ingest.py:268` `title=item_title or f"Episode {episode_number or 1}"`).

**Files:** Modify `audiobiblio/dedupe/matching.py` (promote `GENERIC_TITLES` + `is_generic_title(title: str) -> bool` public — keep old names as aliases); modify `audiobiblio/library/pipelines/ingest.py` (guard in `upsert_from_item`); modify `audiobiblio/library/pipelines/postprocess.py` + `library.py` (never write a generic title into tags/filename stems — verify where stems use ep.title); test `tests/library/test_generic_title_guard.py`.

**Interfaces:** `is_generic_title(title)` — normalized (diacritics-stripped, lowercased) membership in GENERIC_TITLES. In `upsert_from_item`: `if item_title and is_generic_title(item_title): item_title = None` (falls through to the existing `f"Episode {n}"` fallback). In path/tag building: a title failing the guard is treated as absent (album/track fallback rules apply).

- [ ] TDD: ingest with title "Epizody pořadu" (diacritics!) → episode title becomes "Episode N"; normal titles unaffected; existing DB episodes with generic titles are NOT retro-renamed by this task (note: one-off cleanup happens in the gate); filename stem never contains a generic title.
- [ ] Layer check: ingest (library) already imports dedupe ✓. Suite + lint. Docs: dedupe.md + workflows.md §4.1.
- [ ] Commit `fix: generic titles never become episode titles, tags, or filenames`

---

### Task 3: Provenance writers — SCRAPED observations on ingest

Activate `metadata_values`: zero writers exist today.

**Files:** Modify `audiobiblio/core/provenance.py` (add `record_value`); modify `audiobiblio/library/pipelines/ingest.py` (record on upsert); test `tests/core/test_record_value.py`.

**Interfaces:**
- `record_value(session, entity_type: str, entity_id: int, field: str, value: str | None, origin: FieldOrigin, source: str) -> MetadataValue` — upsert on the unique constraint `(entity_type, entity_id, field, origin, source)`: existing row → update `value` + `observed_at=datetime.utcnow()` (project idiom); else insert. No commit (caller's transaction).
- Ingest records for the episode: `title` (post-guard), `description` (summary); for the work: `author`, `title`; origin SCRAPED, source = discovery source string already available in `upsert_from_item` (check the `discovery_source` param — use it, fallback "scrape"). Wrap in try/except (warning log) — provenance failure must never break ingest (same pattern as evaluate_reair isolation).

- [ ] TDD: insert-then-update on same key (observed_at moves, one row); distinct sources coexist; ingest creates SCRAPED rows for a new episode; re-ingest updates not duplicates; provenance failure isolated (monkeypatch record_value to raise → upsert still succeeds).
- [ ] Suite + lint. Docs: core.md + workflows.md §4.6 marker `[partial: SCRAPED writers live]`.
- [ ] Commit `feat: record SCRAPED provenance on ingest`

---

### Task 4: Manual edit API — MANUAL provenance + applied value

**Files:** Modify `audiobiblio/web/routers/episodes.py` (PATCH route); modify `audiobiblio/web/schemas.py`; test `tests/web/test_episode_edit_api.py` (extend web conftest app with episodes router if absent).

**Interfaces:**
- `PATCH /api/v1/episodes/{id}/metadata` body `{"field": str, "value": str}` — allowed fields: episode-level `title`, `description`; work-level `author`, `title` (route as `work.title` vs `title`? Keep flat: allowed set `{"title", "description", "author", "year", "narrator", "genre"}`). Behavior: ALWAYS `record_value(..., origin=MANUAL, source="user")`; ADDITIONALLY apply to the ORM column where one exists (`episode.title`, `episode.summary` for description, `work.author`, `work.title` n/a here, `work.year`); `narrator` and `genre` have NO ORM column — provenance row only (the sync engine projects them onto file tags, which is where they live). Response includes `"applied": bool` reflecting whether an ORM column was updated. 400 unknown field, 404 unknown episode, 422 empty value.
- Response: `{"field", "value", "origin": "manual", "applied": true}`.

- [ ] TDD: manual title edit persists MetadataValue + updates Episode.title; work-level author updates Work; second edit of same field updates the same MANUAL row (upsert); invalid field 400; the resolved winner after a later SCRAPED re-ingest is STILL the manual value (integration with resolve_field: after PATCH then record SCRAPED, resolve_field returns MANUAL — and document that ingest must NOT blindly overwrite row values when a MANUAL row exists: add that guard to ingest's title/author writes — `resolve_field` consulted before applying scraped values to the ORM row; test it).
- [ ] Suite + lint. Docs: web.md.
- [ ] Commit `feat: manual metadata edits with MANUAL provenance protection`

**Note:** the ingest guard in this task is the critical piece — from here on, scraped re-crawls can never clobber user edits.

---

### Task 5: Sync engine — file tags as projections

**Files:** Create `audiobiblio/library/sync.py`; modify `audiobiblio/cli.py` (`sync-tags` command); test `tests/library/test_sync.py`.

**Interfaces:**
- `compute_resolved(session, episode) -> dict[str, str]` — for each known field, gather MetadataValue rows (episode-level + its work's rows for work fields), resolve via `resolve_field`; fall back to current ORM values where no provenance rows exist.
- `sync_episode_tags(session, episode, write: bool = False) -> SyncReport` — frozen dataclass with `diffs: list[FieldDiff(field, file_value, resolved_value, action)]`. Read the episode's COMPLETE audio file tags (`tags.reader.read_tags` — CHECK the m4a path: reader uses exiftool subprocess; if exiftool is absent, degrade to mutagen-based reading for the fields we need, or skip with a warning — decide from the code, document). Actions: file matches resolved → none; file differs & no FILE observation newer → `record_value(origin=FILE, source=file_path)` first (the file's value competes by rank), recompute resolved; final resolved != file value → action "rewrite" (performed only with `write=True` via `tags.writer.write_tags`, mapping like carryover.py). MANUAL always wins (rank), so a hand-edited DB value rewrites the file; a hand-edited FILE (higher rank than SCRAPED) updates the DB projection of scraped-only fields.
- CLI: `audiobiblio sync-tags [--episode-id N | --limit N] [--write]` — default dry-run prints diff table.

- [ ] TDD (use silent_m4a fixtures + db factories): no-provenance no-diff; scraped-only DB vs richer file → FILE recorded, file value wins, no rewrite needed; MANUAL in DB vs different file → rewrite action, file gets manual value on `--write`; idempotent second run → zero diffs; unreadable file → skipped with warning, no crash.
- [ ] Suite + lint (library imports tags — check contract direction: library(tier 2) → tags(tier 3) is DOWNWARD ✓). Docs: library.md + workflows.md §4.6 → `[works today]`.
- [ ] Commit `feat: sync engine — DB-resolved tags projected onto files`

---

### Task 6: Import scanner + `import_findings` table

**Files:** Modify `audiobiblio/core/db/models.py` (+`ImportBucket` str-enum: MATCHED, DUPLICATE, UNKNOWN, CONFLICT; `ImportFinding` model); new migration; create `audiobiblio/library/importer.py`; modify `audiobiblio/core/config.py` (`inbox_dirs: list[str] = field(default_factory=list)` + env `AUDIOBIBLIO_INBOX_DIRS` comma-separated + config.yaml.template); test `tests/library/test_importer.py`.

**Interfaces:**
- `ImportFinding`: `scan_id String(36)`, `path String(2000) unique`, `bucket SAEnum(ImportBucket)`, `episode_id Optional FK`, `details JSON` (tags read, match reason, candidate episode ids), `status String(20) default "new"` ("new" | "accepted" | "ignored"), `created_at`, `resolved_at Optional`.
- `scan_directory(session, root: Path, scan_id: str, inbox: bool = False, limit: int | None = None) -> ScanReport` — walk `find_audio_files`; skip paths already in Asset.file_path (COMPLETE) or already in import_findings with status != "new" (re-scan updates "new" rows); per file: read tags (reader; same exiftool caution as Task 5) + parse folder/filename per NAMING_CONVENTION patterns (author/year/album/track/title — a small `parse_stem(name) -> dict` helper, tested); match against DB:
  1. exact `file_path` on a MISSING asset (dead-path recovery! the file moved — match by basename against `extra["last_known_path"]` basenames too) → MATCHED with reason "path"
  2. title match: normalized episode title (dedupe `_norm_title`) equality or SequenceMatcher > 0.9 within episodes whose work/program matches the parsed album/author, else global cap-bounded → MATCHED (single candidate) with reason "title"; multiple candidates → UNKNOWN with candidates in details
  3. matched episode already has a COMPLETE audio asset with a DIFFERENT existing path → DUPLICATE
  4. no match → UNKNOWN
  Findings persisted; MATCHED does NOT auto-link — linking happens on Accept (Task 7). Generic-title files (is_generic_title on parsed title) noted in details.
- `accept_finding(session, finding, move: bool = False, library_dir: Path | None = None, trash_fn=None) -> list[str]` — link: create/update the episode's AUDIO asset (status COMPLETE, file_path=finding.path — if a MISSING asset exists, repair it and clear last_known_path), `record_value(FILE, ...)` for the file's tags, `apply_media_info`; `move=True` (inbox flow): compute target via `build_paths_for_episode`, `shutil.move` (collision → suffix), then link to the NEW path. DUPLICATE accept = the Phase 3 quality decision: NOT automated here — details in Task 7 (routes to upgrade-style comparison or manual keep, keep scope: duplicate accept just links as an ADDITIONAL asset? NO — Asset unique per (episode,type). Duplicate accept therefore = replace flow: old file to trash via trash_fn, new file linked; refuse without trash_fn).
- `ignore_finding(session, finding)` — status ignored.

- [ ] TDD: parse_stem patterns (4 representative NAMING_CONVENTION shapes); scan buckets (matched-by-title, dead-path-recovery-by-basename, duplicate, unknown, generic-title note); accept links + repairs MISSING + records FILE provenance; accept with move relocates; duplicate accept without trash_fn refuses; ignore; re-scan idempotence (existing "new" findings updated, resolved ones untouched). RED → GREEN.
- [ ] Migration verified both directions. Suite + lint. Docs: library.md + workflows.md §4.3 markers.
- [ ] Commit `feat: import scanner — findings buckets with dead-path recovery`

---

### Task 7: Import page

**Files:** Create `audiobiblio/web/routers/importer.py` (+app include) + `audiobiblio/web/templates/import.html`; modify `views.py` (route `/import`, nav entry after Dedupe), `base.html` nav; test `tests/web/test_import_api.py`.

**Interfaces:** REST under `/api/v1/import`: `POST /scan` body `{"root": str|null, "inbox": bool}` (null root = config library_dir; inbox roots from config inbox_dirs) → task_tracker background scan; `GET /findings?bucket=&status=new`; `POST /findings/{id}/accept` body `{"move": bool}` (router injects trash_fn + library_dir — same layer pattern as dedupe merge); `POST /findings/{id}/ignore`. Page: scan buttons (Library / each inbox dir), bucket tabs with dense tables (path tail, parsed title/author, matched episode link, reason, candidates dropdown for UNKNOWN→manual episode-id assign field, Accept/Accept+Move/Ignore via apiJson). Console: "import findings" count line under the inbox stat when > 0.

- [ ] TDD: endpoints (scan stubbed via task_tracker monkeypatch; accept/ignore against fixtures); route census incl /import.
- [ ] Suite + lint. Docs: web.md. Commit `feat: import page — scan, review buckets, accept/ignore`

---

### Task 8: Episode detail page + preview player + provenance view

The user asked: "how do I find the files, check tags, play them?" — this is that page.

**Files:** Modify `views.py` (route `/episodes/{id}`), create `audiobiblio/web/templates/episode_detail.html`; modify `episodes.html` (titles link to detail) + `_partials/job_rows.html` (episode links); modify `audiobiblio/web/routers/episodes.py` (`GET /api/v1/episodes/{id}/audio` — `FileResponse` of the COMPLETE audio asset, 404 if missing/dead path); test extend `tests/web/test_episode_edit_api.py` + a detail-view test.

**Interfaces:** Detail page (dense): header (title, program › series › work breadcrumb, availability badge); `<audio controls preload="none" src="/api/v1/episodes/{id}/audio">`; files table (assets: type, status badge, file_path with exists-check badge, size, bitrate); metadata table with per-field: resolved value, origin badge (manual=green/enriched=blue/file=gray/scraped=orange), inline edit (click → input → PATCH from Task 4 via apiJson) writing MANUAL; provenance history in a `<details>` (all MetadataValue rows per field); jobs list.

- [ ] TDD: audio endpoint 200 with correct content-type for existing file + 404 for MISSING asset; detail view renders (grouping-function-level test per the established inbox pattern); episodes list links.
- [ ] Suite + lint + route census. Docs: web.md. Commit `feat: episode detail — files, provenance, inline edit, preview player`

---

### Task 9: Phase 4 verification gate

- [ ] Suite + lint + `alembic heads` (one) + cycle for this phase's migration(s).
- [ ] Route census incl /import + a detail page.
- [ ] **Real-data reconciliation:** `verify-files` dry → quote; then `--fix` (user-approved goal of this phase) → ~301 assets MISSING with last_known_path. Then `POST /import/scan` on the real library_dir; quote bucket counts — the dead-path-recovery matcher should reclaim files that still exist under new folder names; accept 2–3 obvious MATCHED findings and verify asset repair (file_path updated, provenance FILE rows created). Document everything; leave the rest for the user's review in the UI.
- [ ] **Sync sample:** `sync-tags --limit 5` dry on real DB → quote diffs; pick ONE episode, PATCH a manual title via API, `sync-tags --episode-id N --write`, read the file back (mutagen) → manual title on file. Revert nothing (manual edit is legitimate data).
- [ ] Generic-title cleanup one-off: count episodes with generic titles in real DB (`is_generic_title`), fix them to "Episode N" via a documented one-off (SQL or small script through record_value SCRAPED? — titles only, quote count).
- [ ] Preview player: curl the audio endpoint of a real episode → 200 audio/mp4.
- [ ] Docs sweep (workflows §4.3/§4.6 markers vs proven reality). Report.

---

## Deferred (recorded)

- Enrichment-origin writers (databazeknih) — Phase 5 (§4.4), plus the user's paste-episode-URL→whole-series flow, completeness/gaps, rename-after-complete-series.
- ABS metadata push absorption — Phase 6.
- Conflict queue as a dedicated Inbox section — CONFLICT bucket lives on the Import page this phase; revisit placement with real usage.
- exiftool dependency decision (if reader's m4a path proves fragile, unify on mutagen) — decide during Tasks 5/6, record as ADR if changed.
