# web — FastAPI dashboard, REST API, and SSE live updates

## Design language

CSS file: `audiobiblio/web/static/audiobiblio.css` (vanilla CSS, no framework dependency).

Based on **infosoud_web tokens**: blue gradient header (`#1a3a5c → #1e4d7b`), white cards on `#f4f6f9` background, compact tables, system font stack.

### CSS classes

| Class | Purpose |
|---|---|
| `.card`, `.card h2` | White card container with shadow (padding 0.9rem, margin-bottom 0.9rem) |
| `.badge`, `.badge-green/blue/orange/red/gray` | Status badges (blue = enriched provenance origin) |
| `.btn`, `.btn-sm`, `.btn-outline`, `.btn-danger` | Button family (btn: 0.35rem 0.8rem; btn-sm: 0.2rem 0.5rem / 0.75rem) |
| `.grid-2`, `.grid-4` | 2-column (gap 0.6rem) and 4-column (gap 0.5rem) responsive grids |
| `.grid` | Auto-fit responsive grid (Pico compat) |
| `.stat`, `.stat-num`, `.stat-num.stat-bad`, `.stat-label` | Console stat cards (stat padding 0.6rem; stat-num 1.5rem) |
| `.pill` | Inbox counter bubble (amber) |
| `.error-box` | Red error callout |
| `.dense-table` | Nowrap + ellipsis for td cells (max-width 28rem); applied to inbox/jobs/targets/index tables |
| `.text-muted`, `.text-sm`, `.mt-1`, `.mb-1` | Utility classes |

### Density values (UI density pass)

Container (`main`, `header .inner`, `.container`): max-width **1440px**. Main margin: **1rem** auto. Header padding: **0.5rem 1.5rem**. Header h1: **1.15rem**. Footer padding: **0.6rem**. Table font-size: **0.8rem**; td/th padding: **0.25rem 0.5rem**. Card padding: **0.9rem**; margin-bottom **0.9rem**; h2 font-size **1rem**. Input/select padding: **0.35rem 0.5rem**. Label margin-bottom: **0.15rem**.

### Nav `active` values

`home`, `inbox`, `targets`, `dedupe`, `import`, `jobs`, `episodes`, `gaps`, `segmentation`, `programs`, `ingest`, `catalog`, `logs`, `system`

### Pico compat rules added

Old templates used Pico CSS structural classes. Compat rules in `audiobiblio.css` cover: `.grid` (auto-fit grid), `article`/`article>header`, `mark`, `ins`, `button.outline`, `button.secondary`, `button.outline.secondary`, `button.small`, `a[role="button"]`, `details`/`summary`, `dialog`, `textarea`, `input[type="url|number|search"]`, `--pico-*` CSS variable fallbacks (`--pico-ins-color`, `--pico-del-color`, `--pico-primary`, `--pico-secondary`), `.stat-val`, `.url-cell`.

### Template API (Starlette 1.3.1)

`views.py` was migrated from the deprecated `TemplateResponse(name, context)` call form to the current `TemplateResponse(request, name, context)` API required by Starlette 1.3.1+.

---

**Layer:** Layer 1 of 5 (top). Imports from all other modules. Nothing in the project imports from `web`. This is an intentional constraint: `web` is a pure consumer, never a provider.
**Standalone use:** `uv run audiobiblio serve [--host HOST] [--port PORT]` — starts FastAPI with the APScheduler background scheduler. Default: `0.0.0.0:8080`.

## Responsibilities

- Application factory `create_app()` wires all API routers, HTML view routes, and static files; starts and shuts down the APScheduler in a FastAPI lifespan context.
- Serves HTML pages rendered by Jinja2 (infosoud_web design language: blue gradient header, white cards on `#f4f6f9`, status badges); HTMX partial updates for live refresh.
- Exposes a REST API under `/api/v1/` consumed by HTMX partials and external callers.
- Provides SSE (`/api/v1/events`) for live download-progress events.
- Approval workflow: `POST /api/v1/jobs/{id}/approve` and `POST /api/v1/jobs/{id}/reject` operate at **episode granularity** — they cascade to every `APPROVAL` job that shares the addressed job's `episode_id` in a single transaction, returning `{"cascaded": n}`. `POST /api/v1/jobs/approve-all` and `POST /api/v1/jobs/reject-all` bulk-update all `APPROVAL` jobs across all episodes (no cascade change needed: they already touch every job). In all cases the addressed job must be in `APPROVAL` status or a 409 is returned.
- Background task tracker (`tasks.py`) for long-running operations triggered from the API (e.g., `POST /api/v1/jobs/run`).

## Public interface

The web module's public surface is its HTTP API and the two entry points used by `cli.py`:

| Name | Signature | Purpose |
|---|---|---|
| `create_app` | `() -> FastAPI` | Application factory |

### HTML pages (views.py)

| Route | Page |
|---|---|
| `GET /` | Console: episode counts, job stats, inbox count, active downloads, failures, sources health (with overdue badge per row and "N sources overdue" counter under failed-jobs stat), disk usage, recent jobs |
| `GET /inbox` | Grouped approval queue — approve/reject individual or all APPROVAL jobs; Upgrades card (PENDING_REVIEW + STAGED candidates) above program groups |
| `GET /jobs` | Downloads page: status filter tabs (all/pending/running/success/error/watch/skipped/approval), SSE-refreshed job rows (named events `run_jobs_completed`/`run_jobs_failed`/`crawl_completed` + 30 s poll fallback), "Run Jobs" and "Retry All Failed" buttons, Watch card, Inbox link when approval_count > 0 |
| `GET /episodes` | Episode browser with search and availability filter; titles link to the detail page |
| `GET /episodes/{episode_id}` | Episode detail: breadcrumb (program › series › work), availability badge, preview player (`<audio controls preload="none">` → `/api/v1/episodes/{id}/audio`), files table (type/status/exists-on-disk badge/path/size/bitrate), metadata table with resolved winner + origin badge + inline edit + per-field provenance history in `<details>`, jobs table. Unknown ID redirects to `/episodes`. |
| `GET /targets` | Sources page — add/edit/delete CrawlTargets; toggle approval_mode (review/auto) and active per target; crawl-now button; inline JS fetch() for JSON-body requests (json-enc extension not loaded) |
| `GET /programs` | Programs grouped by station with job stats |
| `GET /ingest` | Manual URL ingest form — paste any URL to classify; episode URLs surface a "Přidat celý pořad jako zdroj" card |
| `GET /catalog` | Catalog landing: programs with catalog entry counts |
| `GET /catalog/{program_id}` | Per-program gap report |
| `GET /jdownloader` | JDownloader link submission form |
| `GET /logs` | Recent finished download jobs |
| `GET /dedupe` | Duplicate clusters: Tier-A (shared stripped URL) and Tier-B (fuzzy title). Per-pair Preview (dry-run action list in `<details>`) and Merge (hx-confirm, real run) buttons. Query param `limit` (default 200, max 2000). |
| `GET /import` | Import scanner: scan buttons (Library / each inbox dir), bucket tabs (Matched / Duplicate / Unknown) with per-bucket dense tables loaded via JS fetch, Accept / Accept+Move / Ignore per row. Console badge shows `import_count` new findings. |
| `GET /gaps` | Gap report: dense table of Works with `expected_total` set and `have < expected_total` — title, program, have/expected, missing episode numbers (when numbering trustworthy), link to first episode. Empty state when no gaps. Console shows "N gaps in expected totals" link when count > 0. |
| `GET /segmentation` | Segmentation review: program selector, episode-title analysis proposal (mode, proposed works with checkboxes, signal badge, confidence), dry-run preview panel, and apply-with-confirm flow. |
| `GET /system` | System info: version badge; scheduler card (running/stopped badge + jobs table with id and next_run_time); stats block (episodes/jobs/targets counts); ABS card (configured? shows URL + redacted key + [Spustit ABS scan] button via apiJson, else muted hint); config summary (library_dir, download_dir, inbox_dirs, trash_retention_days); link to /logs. |

### REST API routers

| Prefix | Router | Key endpoints |
|---|---|---|
| `/api/v1/jobs` | `routers/jobs.py` | `GET`, `GET /{id}`, `POST /{id}/retry`, `POST /retry-all-failed`, `POST /{id}/approve`, `POST /approve-all`, `POST /{id}/reject`, `POST /reject-all`, `POST /run` |
| `/api/v1/episodes` | `routers/episodes.py` | `GET`, `GET /{id}`, `GET /{id}/audio`, `PATCH /{id}/metadata` |
| `/api/v1/targets` | `routers/targets.py` | `GET`, `POST`, `DELETE /{id}`, `PATCH /{id}` — `approval_mode: "auto"\|"review"` on create/update/response |
| `/api/v1/ingest` | `routers/ingest.py` | `POST /url/preview` (classify + parent probe), `POST /url` (single-episode ingest), `POST /program/preview`, `POST /program` (bulk program ingest), `GET /programs`, `POST /programs/add`, `PATCH /programs/{id}` |
| `/api/v1/catalog` | `routers/catalog.py` | `GET`, `POST /{program_id}/scrape` |
| `/api/v1/system` | `routers/system.py` | `GET /health`, `GET /stats`, `GET /system/scheduler` (running + jobs list), `POST /system/abs-scan` |
| `/api/v1/events` | `routers/sse.py` | SSE event stream |
| `/api/v1/jdownloader` | `routers/jdownloader.py` | Submit links to JDownloader |
| `/api/v1/upgrades` | `routers/upgrades.py` | `GET ?status=`, `POST /{id}/stage`, `POST /{id}/resolve` |
| `/api/v1/dedupe` | `routers/dedupe.py` | `POST /merge` — merge duplicate into canonical; 409 if MANUAL metadata rows on duplicate |
| `/api/v1/import` | `routers/importer.py` | `POST /scan`, `GET /findings?bucket=&status=new`, `POST /findings/{id}/accept`, `POST /findings/{id}/ignore` |
| `/api/v1/works` | `routers/works.py` | `PATCH /{id}` — set or clear `expected_total`; body `{"expected_total": N}` sets it (MANUAL provenance + ORM), body `{"expected_total": null}` clears both column and source, recording a MANUAL provenance row with value=None; 422 for non-positive integers, 404 for unknown work |
| `/api/v1/segmentation` | `routers/segmentation.py` | `GET /{program_id}` — proposal JSON (mode, proposed works, unassigned_count, note); `POST /{program_id}/apply` — apply or dry-run with optional titles filter; 404 if program not found |

#### Paste-URL preview endpoint (Phase 5 Task 5)

`POST /api/v1/ingest/url/preview` body `{"url": str}`

Classifies any pasted URL without live crawling: calls `probe_url` + `classify_probe` from `mrz_inspector`. For episode URLs (mujrozhlas depth ≥ 2), also probes the parent program URL (one extra sequential call) and returns a `parent` block so the UI can offer "add whole program as a source".

Response shape (additive extension of `IngestPreviewResponse`):
```json
{
  "raw_count": 1,
  "unique_count": 1,
  "reairs": 0,
  "already_in_db": 0,
  "rozhlas_extra": 0,
  "episodes": [{"title": "...", "url": "...", "series": "..."}],
  "kind": "episode",
  "parent": {"url": "https://www.mujrozhlas.cz/program-slug", "title": "Název pořadu", "episode_count": 42}
}
```

`parent` is `null` when the URL is already a program/series URL or non-mujrozhlas.

When the parent URL is detected but probing fails, `parent` is returned with `title: null` and `episode_count: 0` (degraded). The UI may still use this to offer "Add whole program" with limited information.

`parent_url(url: str) -> str | None` in `sources/mrz_inspector.py`: derives program root from episode URL using `_mrz_parts` — depth ≥ 2 returns `scheme://host/parts[0]`; depth < 2 or non-mrz returns `None`.

#### Manual metadata edit endpoint

`PATCH /api/v1/episodes/{id}/metadata` body `{"field": str, "value": str}`

Allowed fields: `title`, `description` (episode-level ORM); `author`, `year` (Work-level ORM); `narrator`, `genre` (provenance-only — no ORM column, sync engine projects to file tags).

- ALWAYS calls `record_value(..., MANUAL, "user")` — upserts the MANUAL provenance row.
- ADDITIONALLY updates the ORM column where one exists (`episode.title`, `episode.summary` for description, `work.author`, `work.year`).
- `year` value must be int-castable (422 otherwise); stored as string in `MetadataValue`, applied as `int` to `work.year`.
- `applied: bool` in response indicates whether an ORM column was updated.
- Errors: 400 unknown field, 404 episode not found, 422 empty/whitespace value or non-integer year.
- Once a MANUAL row exists, ingest can never silently overwrite it: the `has_manual()` guard in `library/pipelines/ingest.py` checks before applying scraped title/author to ORM columns; the SCRAPED observation is still recorded (it loses by rank).

Response: `{"field", "value", "origin": "manual", "applied": bool}`.

#### Audio preview endpoint

`GET /api/v1/episodes/{id}/audio` — `FileResponse` of the episode's **COMPLETE** audio asset for the detail-page preview player.

- 404 when the episode is unknown, no audio asset exists, the asset is not `COMPLETE`, or the file is gone from disk (per-request `is_file()` check).
- `Content-Type` by suffix: `.m4a`/`.m4b` → `audio/mp4`, `.mp3` → `audio/mpeg`, anything else → `application/octet-stream`.
- **Seeking works**: Starlette 1.3.1's `FileResponse` handles HTTP `Range` requests natively (206 Partial Content, `Accept-Ranges`), verified by test — no custom range handling was added.

#### Episode detail page

`GET /episodes/{episode_id}` renders `episode_detail.html` (dense design, `active='episodes'`):

- **Header card**: `Library › program › series › work` breadcrumb, `#id title`, availability badge (available=green / gone=red / unavailable=orange / unknown=gray), duration, published date, source-page link, and the `<audio controls preload="none">` player (hidden with a hint when no complete audio file is on disk).
- **Files card**: one row per Asset — type, status badge, exists badge (`on disk`=green / `not found`=red / `no path`=gray), path, human size, bitrate.
- **Metadata & provenance card**: one row per editable field (`title, author, narrator, genre, description, year`) built by `_episode_metadata_rows(db, ep)` (unit-testable like `_group_approval_jobs`): current ORM value, resolved winner via `resolve_field()`, origin badge (**manual=badge-green, enriched=badge-blue, file=badge-gray, scraped=badge-orange**), full observation history in a `<details>`, and an inline edit button. Edit builds the input via `createElement` (no innerHTML interpolation) and PATCHes `/api/v1/episodes/{id}/metadata` through `apiJson()` — always recorded as MANUAL. `author`/`year` provenance is read from the **Work** entity, everything else from the Episode (same routing as the PATCH endpoint).
- **Jobs card**: the episode's download jobs, reusing `_partials/job_rows.html`.

Links in: `/episodes` list titles and `_partials/job_row.html` episode names link to `/episodes/{id}`.

`audiobiblio.js` also hosts the shared `escHtml()` HTML-escaping helper (promoted from `import.html`, which now uses the shared copy).

#### Upgrade lifecycle endpoints

| Endpoint | Description |
|---|---|
| `GET /api/v1/upgrades?status=pending_review` | List `UpgradeCandidate` rows, optionally filtered by status (`pending_review`, `staged`, `replaced`, `kept_old`, `dismissed`). Returns `{items, total, limit, offset}`. 400 on invalid status. |
| `POST /api/v1/upgrades/{id}/stage` | Submit a background task (`task_tracker`) that calls `download_to_staging(url, {download_dir}/_staging/upgrade-{id}/)`, sets status `STAGED`, stores `staged_path`, and records bitrate/duration in `note`. Returns 202 `{task_id, name, status}`. 409 if candidate is not `PENDING_REVIEW`. |
| `POST /api/v1/upgrades/{id}/resolve` | Resolve the candidate. Body: `{"decision": "replace" \| "keep_old" \| "dismiss"}`. Returns `{id, status, resolved_at}`. |

**`replace` semantics** (requires `STAGED` status; 409 otherwise):

1. `carry_over_tags(old → staged)` — carries curated tags from the old file to the staged replacement.
2. `move_to_trash(old, library_dir)` — old file moved to `{library_dir}/.trash/{date}/`; never deleted.
3. `shutil.move(staged → old's exact library path)` — staged file replaces old at same location; `file_path` unchanged on Asset.
4. `apply_media_info(asset, old_path)` — re-reads bitrate/codec/duration from the new file.
5. Status `REPLACED` + `resolved_at` + commit.

**Crash-safety design**: Steps execute in strict order with no automatic rollback. If the server crashes between steps 2 and 3, the old file is safe in the dated trash folder and the staged file remains in the staging directory. Both are fully recoverable. The user must manually re-run resolve after verifying or restoring the staged file to `staged_path`.

**`keep_old` semantics**: Trashes the staged file (if any); status `KEPT_OLD`. Allowed from any non-terminal status.

**`dismiss` semantics**: Same as `keep_old` but sets status `DISMISSED`. Also allowed before staging (no staged file needed).

### Inbox upgrades card

`GET /inbox` now renders an "Upgrades" card above the approval groups (anchored as `id="upgrades"`). The card lists all `PENDING_REVIEW` and `STAGED` `UpgradeCandidate` rows with episode title, owned/candidate duration (formatted by `_fmt_duration_ms`), signed duration diff ("+2:02 ⚠ possible ads" when candidate is longer), a link to the candidate URL, the status badge, and action buttons:

- **PENDING_REVIEW** → `[Stage & compare]` — `POST /api/v1/upgrades/{id}/stage`, then reload.
- **STAGED** → `[Replace]` (JS confirm + `POST .../resolve {decision:"replace"}`), `[Keep old]`, `[Dismiss]`.

The Console stat card for "awaiting approval" shows a small badge-line "N upgrade pairs" linking to `/inbox#upgrades` when `upgrade_count > 0`.

`audiobiblio.js` (new) contains the shared `apiJson(method, url, body)` helper loaded via `base.html` — all pages now have access to it. The previous inline copy in `targets.html` has been removed.

## Files

| File | Purpose |
|---|---|
| `app.py` | `create_app()` — application factory + lifespan (scheduler start/stop, DB init, seed) |
| `views.py` | Jinja2 HTML page routes; `_fmt_duration_ms(ms)`, `_fmt_size(bytes)` helpers; `_query_upgrade_candidates(db)`, `_episode_metadata_rows(db, ep)`, `_episode_asset_rows(ep)`, `_compute_overdue_count(targets, now)` |
| `static/audiobiblio.js` | Shared `apiJson()` fetch helper + `escHtml()` HTML escaper |
| `schemas.py` | Pydantic request/response models (`JobResponse`, `PaginatedJobs`, `TaskResponse`, `UpgradeCandidateResponse`, …) |
| `deps.py` | `get_db()` FastAPI dependency |
| `tasks.py` | `task_tracker` — in-process background task queue |
| `sse.py` | SSE event bus |
| `routers/jobs.py` | Download job CRUD, retry, approve, reject |
| `routers/episodes.py` | Episode listing and detail |
| `routers/targets.py` | CrawlTarget CRUD |
| `routers/ingest.py` | URL ingest endpoint |
| `routers/catalog.py` | Catalog scrape + view |
| `routers/system.py` | Health + stats + scheduler status (`GET /api/v1/system/scheduler`) + ABS scan trigger |
| `templates/system.html` | System page: version, scheduler, stats, ABS, config summary |
| `routers/sse.py` | SSE stream endpoint |
| `routers/jdownloader.py` | JDownloader link submission |
| `routers/upgrades.py` | Upgrade candidate lifecycle (list / stage / resolve) |
| `routers/dedupe.py` | Dedupe merge endpoint (`POST /api/v1/dedupe/merge`) |
| `templates/dedupe.html` | Duplicate clusters page with Preview + Merge HTMX buttons |
| `templates/episode_detail.html` | Episode detail: preview player, files table, metadata + provenance, inline edit, jobs |

## Planned (phase N)

- **Phase 2 (done):** Inbox page — approve/reject individual or bulk APPROVAL-status jobs; full infosoud_web UI shell with Console (Inbox count, active downloads, per-source health, gaps counter, disk space).
- **Phase 2 (done):** Sources page — CrawlTarget CRUD with auto-vs-review switch per target; crawl-now; add/delete targets.
- **Phase 2 (done):** Downloads page — status filter tabs, SSE live refresh (named events + 30 s poll), Watch card, Run Jobs + Retry All Failed buttons.
- **Phase 3 (done):** Dedupe page — duplicate clusters with Tier-A (shared stripped URL) and Tier-B (fuzzy title), dry-run merge preview, real merge with MANUAL-metadata guard, `GET /dedupe` view, `POST /api/v1/dedupe/merge` endpoint.
- **Phase 4:** Import page — legacy/unsorted scanner with three-bucket review.
- **Phase 4:** Tags page — web tag-fixer with current vs proposed side-by-side diff.
- **Phase 5:** Library page — DB-view browse/search of works/episodes with completeness badges and gap report.
- **Phase 6 (done):** System page — scheduler status, version, stats, ABS config + scan trigger, config summary.
- **Phase 6:** Extract `cli.serve` → `web/__main__.py` to remove the parked `cli → web` import-linter violation.
