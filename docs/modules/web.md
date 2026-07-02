# web — FastAPI dashboard, REST API, and SSE live updates

## Design language

CSS file: `audiobiblio/web/static/audiobiblio.css` (vanilla CSS, no framework dependency).

Based on **infosoud_web tokens**: blue gradient header (`#1a3a5c → #1e4d7b`), white cards on `#f4f6f9` background, compact tables, system font stack.

### CSS classes produced by Task 5

| Class | Purpose |
|---|---|
| `.card`, `.card h2` | White card container with shadow |
| `.badge`, `.badge-green/orange/red/gray` | Status badges |
| `.btn`, `.btn-sm`, `.btn-outline`, `.btn-danger` | Button family |
| `.grid-2`, `.grid-4` | 2- and 4-column responsive grids |
| `.grid` | Auto-fit responsive grid (Pico compat) |
| `.stat`, `.stat-num`, `.stat-num.stat-bad`, `.stat-label` | Console stat cards |
| `.pill` | Inbox counter bubble (amber) |
| `.error-box` | Red error callout |
| `.text-muted`, `.text-sm`, `.mt-1`, `.mb-1` | Utility classes |

### Nav `active` values

`home`, `inbox`, `targets`, `jobs`, `episodes`, `programs`, `ingest`, `catalog`, `logs`

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
- Provides SSE (`/api/v1/sse`) for live download-progress events.
- Approval workflow: `POST /api/v1/jobs/{id}/approve` and `POST /api/v1/jobs/approve-all` move jobs from `APPROVAL` to `PENDING` status so the scheduler picks them up. `POST /api/v1/jobs/{id}/reject` and `POST /api/v1/jobs/reject-all` move jobs from `APPROVAL` to `SKIPPED` status.
- Background task tracker (`tasks.py`) for long-running operations triggered from the API (e.g., `POST /api/v1/jobs/run`).

## Public interface

The web module's public surface is its HTTP API and the two entry points used by `cli.py`:

| Name | Signature | Purpose |
|---|---|---|
| `create_app` | `() -> FastAPI` | Application factory |

### HTML pages (views.py)

| Route | Page |
|---|---|
| `GET /` | Console: episode counts, job stats, recent jobs |
| `GET /inbox` | Grouped approval queue — approve/reject individual or all APPROVAL jobs |
| `GET /jobs` | Downloads page: status filter tabs (all/pending/running/success/error/watch/skipped/approval), SSE-refreshed job rows (named events `run_jobs_completed`/`run_jobs_failed`/`crawl_completed` + 30 s poll fallback), "Run Jobs" and "Retry All Failed" buttons, Watch card, Inbox link when approval_count > 0 |
| `GET /episodes` | Episode browser with search and availability filter |
| `GET /targets` | Sources page — add/edit/delete CrawlTargets; toggle approval_mode (review/auto) and active per target; crawl-now button; inline JS fetch() for JSON-body requests (json-enc extension not loaded) |
| `GET /programs` | Programs grouped by station with job stats |
| `GET /ingest` | Manual URL ingest form |
| `GET /catalog` | Catalog landing: programs with catalog entry counts |
| `GET /catalog/{program_id}` | Per-program gap report |
| `GET /jdownloader` | JDownloader link submission form |
| `GET /logs` | Recent finished download jobs |

### REST API routers

| Prefix | Router | Key endpoints |
|---|---|---|
| `/api/v1/jobs` | `routers/jobs.py` | `GET`, `GET /{id}`, `POST /{id}/retry`, `POST /retry-all-failed`, `POST /{id}/approve`, `POST /approve-all`, `POST /{id}/reject`, `POST /reject-all`, `POST /run` |
| `/api/v1/episodes` | `routers/episodes.py` | `GET`, `GET /{id}` |
| `/api/v1/targets` | `routers/targets.py` | `GET`, `POST`, `DELETE /{id}`, `PATCH /{id}` — `approval_mode: "auto"\|"review"` on create/update/response |
| `/api/v1/ingest` | `routers/ingest.py` | `POST` (URL ingest) |
| `/api/v1/catalog` | `routers/catalog.py` | `GET`, `POST /{program_id}/scrape` |
| `/api/v1/system` | `routers/system.py` | Health check, scheduler status |
| `/api/v1/sse` | `routers/sse.py` | SSE event stream |
| `/api/v1/jdownloader` | `routers/jdownloader.py` | Submit links to JDownloader |

## Files

| File | Purpose |
|---|---|
| `app.py` | `create_app()` — application factory + lifespan (scheduler start/stop, DB init, seed) |
| `views.py` | Jinja2 HTML page routes |
| `schemas.py` | Pydantic request/response models (`JobResponse`, `PaginatedJobs`, `TaskResponse`, …) |
| `deps.py` | `get_db()` FastAPI dependency |
| `tasks.py` | `task_tracker` — in-process background task queue |
| `sse.py` | SSE event bus |
| `routers/jobs.py` | Download job CRUD, retry, approve, reject |
| `routers/episodes.py` | Episode listing and detail |
| `routers/targets.py` | CrawlTarget CRUD |
| `routers/ingest.py` | URL ingest endpoint |
| `routers/catalog.py` | Catalog scrape + view |
| `routers/system.py` | Health + scheduler status |
| `routers/sse.py` | SSE stream endpoint |
| `routers/jdownloader.py` | JDownloader link submission |

## Planned (phase N)

- **Phase 2 (done):** Inbox page — approve/reject individual or bulk APPROVAL-status jobs; full infosoud_web UI shell with Console (Inbox count, active downloads, per-source health, gaps counter, disk space).
- **Phase 2 (done):** Sources page — CrawlTarget CRUD with auto-vs-review switch per target; crawl-now; add/delete targets.
- **Phase 2 (done):** Downloads page — status filter tabs, SSE live refresh (named events + 30 s poll), Watch card, Run Jobs + Retry All Failed buttons.
- **Phase 3:** Dedupe page — duplicate clusters, quality comparison, merge tool.
- **Phase 4:** Import page — legacy/unsorted scanner with three-bucket review.
- **Phase 4:** Tags page — web tag-fixer with current vs proposed side-by-side diff.
- **Phase 5:** Library page — DB-view browse/search of works/episodes with completeness badges and gap report.
- **Phase 6:** System page — scheduler status, logs, job history, config editor.
- **Phase 2:** Extract `cli.serve` → `web/__main__.py` to remove the parked `cli → web` import-linter violation.
