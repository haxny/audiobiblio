# Phase 2: Daily Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The daily monitor → review → approve → download loop becomes pleasant: per-target auto/review switch, an Inbox page, and the web UI rebuilt in the infosoud design language. Daily real-life use starts at the end of this phase.

**Architecture:** Extends the Phase 1 foundation (spec §4.1, §5). The existing threshold-based approval (`APPROVAL_THRESHOLD = 3` in `library/pipelines/checks.py`) becomes the fallback; an explicit `approval_mode` on CrawlTarget takes precedence and is threaded crawl → ingest → `plan_downloads`. The UI keeps FastAPI + Jinja2 + HTMX + the existing SSE/TaskTracker plumbing, but replaces Pico CSS with a hand-written stylesheet adapted from infosoud_web.

**Tech Stack:** Python ≥3.10, FastAPI, Jinja2, HTMX 2 (+sse ext), SQLAlchemy 2, Alembic, pytest, uv.

## Global Constraints

- New Alembic migration chains from current head `059c3c38a79a`; adds a column, never restarts schema.
- All existing routes keep working throughout (old pages may look plainer under the new CSS until rebuilt — but must render and function).
- Suite green via `uv run pytest -q` (currently 53); `uv run lint-imports` KEPT (4 parked violations unchanged — do not add new ones; web→anything is allowed, it's the top layer).
- Design tokens (from ~/projects/rejstriky/infosoud_web, verbatim where possible): header gradient `linear-gradient(135deg, #1a3a5c 0%, #1e4d7b 100%)`; body bg `#f4f6f9`; text `#1a1a2e`; cards `#fff`, radius 8px, shadow `0 1px 4px rgba(0,0,0,0.08)`; primary `#1e4d7b`, hover `#163d63`, input focus `#3b82f6`; badges green `#dcfce7/#166534`, orange `#fef3c7/#92400e`, red `#fef2f2/#991b1b`, gray `#f3f4f6/#4b5563`; system font stack; main `max-width: 1000px`.
- Web tests use a dedicated FastAPI app with routers + `app.dependency_overrides[get_db]` — never `create_app()` (its lifespan starts the scheduler and seeds the DB).
- Docs are part of done: tasks that change module behavior update `docs/modules/*.md` and `docs/workflows.md` in the same commit.
- Commits `<type>: <description>`, no AI attribution. Working dir `/Users/jirislovacek/projects/audiobiblio`, work on a NEW branch `feature/phase2-daily-loop` off main.

---

### Task 1: DB test fixtures + plan_downloads characterization tests

There are no DB-backed tests yet. Build the in-memory fixture every later task uses, and pin the CURRENT approval-threshold behavior before changing it.

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/library/__init__.py` (empty)
- Test: `tests/library/test_plan_downloads.py`

**Interfaces:**
- Consumes: `audiobiblio.library.pipelines.checks.plan_downloads(session, episode_id) -> list[DownloadJob]`, `APPROVAL_THRESHOLD = 3`, models from `audiobiblio.core.db.models`.
- Produces: fixtures `db_session` (in-memory SQLite Session) and `episode_factory(program_name=..., with_audio_asset=True) -> Episode` used by Tasks 2, 3, 4.

- [ ] **Step 1: Read `audiobiblio/library/pipelines/checks.py` fully.** Determine whether `plan_downloads` creates Asset rows itself or expects them to exist. Adapt ONLY the fixture below to that reality (e.g. drop `with_audio_asset` if assets are auto-created) — the asserted behaviors don't change.

- [ ] **Step 2: Write the fixtures**

Create `tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Base, Episode, Program, Series, Station, Work,
)


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def episode_factory(db_session):
    """Create Episode with full Station->Program->Series->Work hierarchy.

    Episodes made with the same program_name share one Program (needed for
    the per-program approval-threshold tests).
    """
    counter = {"n": 0}

    def make(program_name: str = "Prog", with_audio_asset: bool = True) -> Episode:
        counter["n"] += 1
        n = counter["n"]
        station = db_session.query(Station).filter_by(code="tst").one_or_none()
        if station is None:
            station = Station(code="tst", name="Test Station")
            db_session.add(station)
            db_session.flush()
        program = db_session.query(Program).filter_by(name=program_name).one_or_none()
        if program is None:
            program = Program(station_id=station.id, name=program_name)
            db_session.add(program)
            db_session.flush()
        series_name = f"{program_name} S"
        series = db_session.query(Series).filter_by(
            program_id=program.id, name=series_name).one_or_none()
        if series is None:
            series = Series(program_id=program.id, name=series_name)
            db_session.add(series)
            db_session.flush()
        work = Work(series_id=series.id, title=f"Work {n}")
        db_session.add(work)
        db_session.flush()
        ep = Episode(work_id=work.id, title=f"Episode {n}", ext_id=f"ext-{n}",
                     url=f"https://example.cz/ep-{n}")
        db_session.add(ep)
        db_session.flush()
        if with_audio_asset:
            db_session.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                                 status=AssetStatus.MISSING))
            db_session.flush()
        return ep

    return make
```

- [ ] **Step 3: Write the characterization tests**

Create `tests/library/__init__.py` (empty) and `tests/library/test_plan_downloads.py`:

```python
"""Characterization: current threshold-based approval in plan_downloads.

First jobs in a fresh program require approval; once a program has
APPROVAL_THRESHOLD jobs in SUCCESS/PENDING/RUNNING, new jobs go straight
to PENDING. Pinned before Task 3 adds the per-target override.
"""
from audiobiblio.core.db.models import DownloadJob, JobStatus
from audiobiblio.library.pipelines.checks import APPROVAL_THRESHOLD, plan_downloads


def test_fresh_program_requires_approval(db_session, episode_factory):
    ep = episode_factory(program_name="Fresh")
    jobs = plan_downloads(db_session, ep.id)
    assert jobs, "expected at least one job for a MISSING asset"
    assert all(j.status == JobStatus.APPROVAL for j in jobs)


def test_established_program_auto_pends(db_session, episode_factory):
    # Seed the program with APPROVAL_THRESHOLD successful jobs
    for _ in range(APPROVAL_THRESHOLD):
        prior = episode_factory(program_name="Known")
        db_session.add(DownloadJob(episode_id=prior.id, asset_type=prior.assets[0].type,
                                   status=JobStatus.SUCCESS))
    db_session.flush()
    ep = episode_factory(program_name="Known")
    jobs = plan_downloads(db_session, ep.id)
    assert jobs
    assert all(j.status == JobStatus.PENDING for j in jobs)
```

- [ ] **Step 4: Run** `uv run pytest tests/library/ -v` — expect PASS (characterization; a failure is a discovery, report it). If `plan_downloads` needs adjustments to the fixture per Step 1, make them and re-run.

- [ ] **Step 5: Commit** `test: DB fixtures + plan_downloads approval-threshold characterization`

---

### Task 2: `approval_mode` on CrawlTarget (model + migration + API)

**Files:**
- Modify: `audiobiblio/core/db/models.py` (add `ApprovalMode` enum + column on CrawlTarget)
- Create: `migrations/versions/<generated>_add_crawl_target_approval_mode.py`
- Modify: `audiobiblio/web/schemas.py` (`TargetCreateRequest`, `TargetUpdateRequest`, `TargetResponse` gain `approval_mode`)
- Modify: `audiobiblio/web/routers/targets.py` (create/update handle the field)
- Test: `tests/core/test_approval_mode.py`, extend `tests/` for targets router: `tests/web/__init__.py`, `tests/web/conftest.py`, `tests/web/test_targets_api.py`

**Interfaces:**
- Produces: `audiobiblio.core.db.models.ApprovalMode` — str-Enum `AUTO = "auto"`, `REVIEW = "review"`; `CrawlTarget.approval_mode: Mapped[ApprovalMode]` default `REVIEW`. API accepts/returns `approval_mode: "auto"|"review"`.

- [ ] **Step 1: Write the failing model test**

Create `tests/core/test_approval_mode.py`:

```python
from audiobiblio.core.db.models import ApprovalMode, CrawlTarget, CrawlTargetKind


def test_default_is_review(db_session):
    t = CrawlTarget(url="https://mujrozhlas.cz/ctenarsky-denik",
                    kind=CrawlTargetKind.PROGRAM)
    db_session.add(t)
    db_session.flush()
    assert t.approval_mode == ApprovalMode.REVIEW


def test_auto_roundtrip(db_session):
    t = CrawlTarget(url="https://mujrozhlas.cz/hra-na-nedeli",
                    kind=CrawlTargetKind.PROGRAM, approval_mode=ApprovalMode.AUTO)
    db_session.add(t)
    db_session.flush()
    db_session.expire(t)
    assert t.approval_mode == ApprovalMode.AUTO
```

Run `uv run pytest tests/core/test_approval_mode.py -v` — FAIL (ImportError: ApprovalMode).

- [ ] **Step 2: Add enum + column** in `audiobiblio/core/db/models.py` (enum next to the other enums; column on CrawlTarget after `active`):

```python
class ApprovalMode(str, Enum):
    """Per-target policy for newly discovered episodes."""
    AUTO = "auto"      # queue downloads immediately (PENDING)
    REVIEW = "review"  # hold in Inbox until approved (APPROVAL)
```

```python
    approval_mode: Mapped[ApprovalMode] = mapped_column(
        SAEnum(ApprovalMode), default=ApprovalMode.REVIEW,
        server_default="REVIEW", nullable=False,
    )
```

(Note: SAEnum stores enum NAMES — `"REVIEW"` — hence the server_default; this matches the existing FieldOrigin behavior, see ledger note.)

Run the test — PASS.

- [ ] **Step 3: Migration**

```bash
uv run alembic revision --autogenerate -m "add crawl_target approval_mode"
```

Verify: `down_revision = '059c3c38a79a'`; upgrade adds ONLY the `approval_mode` column to `crawl_targets` with `server_default='REVIEW'`, `nullable=False`; strip any unrelated autogenerate noise (same three drift ops as Task 7 of Phase 1 may reappear — remove them, note in the migration comment). Then `uv run alembic upgrade head`, `uv run alembic downgrade -1 && uv run alembic upgrade head`.

- [ ] **Step 4: API — failing router test first**

Create `tests/web/__init__.py` (empty) and `tests/web/conftest.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audiobiblio.web.deps import get_db


@pytest.fixture()
def client(db_session):
    """Test app with routers only — create_app() would start the scheduler."""
    from audiobiblio.web.routers import jobs, targets

    app = FastAPI()
    app.include_router(targets.router)
    app.include_router(jobs.router)

    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)
```

(If the routers use a different dependency name/pattern than `get_db`, check `audiobiblio/web/routers/targets.py` imports and override the actual one.)

Create `tests/web/test_targets_api.py`:

```python
def test_create_target_with_approval_mode(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/hra-na-nedeli",
        "kind": "program", "approval_mode": "auto",
    })
    assert r.status_code == 201
    assert r.json()["approval_mode"] == "auto"


def test_create_target_defaults_to_review(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/cetba-na-pokracovani", "kind": "program",
    })
    assert r.status_code == 201
    assert r.json()["approval_mode"] == "review"


def test_patch_approval_mode(client):
    r = client.post("/api/v1/targets", json={
        "url": "https://mujrozhlas.cz/x", "kind": "program"})
    tid = r.json()["id"]
    r2 = client.patch(f"/api/v1/targets/{tid}", json={"approval_mode": "auto"})
    assert r2.status_code == 200
    assert r2.json()["approval_mode"] == "auto"
```

Run — FAIL (422/field missing). Then implement: add `approval_mode: str = "review"` to `TargetCreateRequest`, `approval_mode: str | None = None` to `TargetUpdateRequest`, `approval_mode: str` to `TargetResponse` (serialize `t.approval_mode.value`); in the router, validate via `ApprovalMode(body.approval_mode)` (400 on ValueError) and set/patch the column. Run — PASS.

- [ ] **Step 5: Full suite + lint** `uv run pytest -q && uv run lint-imports` — green/KEPT.

- [ ] **Step 6: Docs + commit.** Update `docs/modules/core.md` (ApprovalMode in models table) and `docs/modules/web.md` (targets API fields). Commit `feat: per-target approval_mode (auto|review) on CrawlTarget + API`

---

### Task 3: Thread approval_mode through crawl → plan_downloads

**Files:**
- Modify: `audiobiblio/library/pipelines/checks.py` (`plan_downloads` gains `approval_mode` param)
- Modify: `audiobiblio/library/pipelines/ingest.py` (`queue_assets_for_episode` passes it through)
- Modify: `audiobiblio/acquire/crawler.py` (pass `target.approval_mode` at every `queue_assets_for_episode` call site)
- Test: extend `tests/library/test_plan_downloads.py`

**Interfaces:**
- Produces: `plan_downloads(session, episode_id: int, approval_mode: ApprovalMode | None = None)` — AUTO → PENDING; REVIEW → APPROVAL; None → legacy threshold logic (manual ingest keeps current behavior). Same signature change on `queue_assets_for_episode`.

- [ ] **Step 1: Failing tests** (append to `tests/library/test_plan_downloads.py`):

```python
from audiobiblio.core.db.models import ApprovalMode


def test_auto_mode_overrides_threshold(db_session, episode_factory):
    ep = episode_factory(program_name="FreshAuto")  # fresh program, no history
    jobs = plan_downloads(db_session, ep.id, approval_mode=ApprovalMode.AUTO)
    assert jobs and all(j.status == JobStatus.PENDING for j in jobs)


def test_review_mode_overrides_established_program(db_session, episode_factory):
    for _ in range(APPROVAL_THRESHOLD):
        prior = episode_factory(program_name="KnownReview")
        db_session.add(DownloadJob(episode_id=prior.id, asset_type=prior.assets[0].type,
                                   status=JobStatus.SUCCESS))
    db_session.flush()
    ep = episode_factory(program_name="KnownReview")
    jobs = plan_downloads(db_session, ep.id, approval_mode=ApprovalMode.REVIEW)
    assert jobs and all(j.status == JobStatus.APPROVAL for j in jobs)


def test_none_keeps_legacy_threshold(db_session, episode_factory):
    ep = episode_factory(program_name="FreshLegacy")
    jobs = plan_downloads(db_session, ep.id, approval_mode=None)
    assert jobs and all(j.status == JobStatus.APPROVAL for j in jobs)
```

Run — FAIL (unexpected keyword argument).

- [ ] **Step 2: Implement.** In `checks.py`:

```python
def plan_downloads(session, episode_id: int,
                   approval_mode: "ApprovalMode | None" = None) -> list[DownloadJob]:
    ...
    if approval_mode is ApprovalMode.AUTO:
        initial_status = JobStatus.PENDING
    elif approval_mode is ApprovalMode.REVIEW:
        initial_status = JobStatus.APPROVAL
    else:
        program_approved = _program_has_approved_jobs(session, episode_id)
        initial_status = JobStatus.PENDING if program_approved else JobStatus.APPROVAL
```

(import `ApprovalMode` from `audiobiblio.core.db.models`; replace the existing two-line decision, keep everything else identical). Mirror the param on `queue_assets_for_episode(session, episode_id, approval_mode=None)` in `ingest.py`, forwarding it.

- [ ] **Step 3: Crawler call sites.** `grep -n "queue_assets_for_episode" audiobiblio/acquire/crawler.py` — at EVERY hit inside `crawl_target` (and helpers it calls with the target in scope), pass `approval_mode=target.approval_mode`. If a helper doesn't receive `target`, thread the parameter down explicitly (no globals). Manual-ingest paths (`web/routers/ingest.py`, `cli.py` ingest commands) stay untouched → None → legacy behavior.

- [ ] **Step 4: Run** `uv run pytest -q && uv run lint-imports` — all green, KEPT (checks.py is in library; importing core is downward — legal).

- [ ] **Step 5: Docs + commit.** Update `docs/workflows.md` §4.1 step 5 marker from `[partial: …]` to `[works today]`; update `docs/modules/library.md` + `acquire.md` signatures. Commit `feat: thread per-target approval_mode into download planning`

---

### Task 4: Reject endpoints for the Inbox

Approve exists (`POST /api/v1/jobs/{id}/approve`, `/approve-all`); reject doesn't. Rejected = `JobStatus.SKIPPED` with a reason.

**Files:**
- Modify: `audiobiblio/web/routers/jobs.py`
- Test: `tests/web/test_jobs_api.py`

**Interfaces:**
- Produces: `POST /api/v1/jobs/{job_id}/reject` (APPROVAL→SKIPPED, reason="rejected in inbox", 409 if not APPROVAL) and `POST /api/v1/jobs/reject-all` (bulk, returns count). Task 7's Inbox UI calls these.

- [ ] **Step 1: Failing tests**

Create `tests/web/test_jobs_api.py`:

```python
from audiobiblio.core.db.models import DownloadJob, JobStatus


def _mk_approval_job(db_session, episode_factory):
    ep = episode_factory()
    job = DownloadJob(episode_id=ep.id, asset_type=ep.assets[0].type,
                      status=JobStatus.APPROVAL)
    db_session.add(job)
    db_session.flush()
    return job


def test_reject_sets_skipped(client, db_session, episode_factory):
    job = _mk_approval_job(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{job.id}/reject")
    assert r.status_code == 200
    db_session.expire(job)
    assert job.status == JobStatus.SKIPPED
    assert "reject" in (job.reason or "").lower()


def test_reject_non_approval_conflicts(client, db_session, episode_factory):
    job = _mk_approval_job(db_session, episode_factory)
    job.status = JobStatus.SUCCESS
    db_session.flush()
    assert client.post(f"/api/v1/jobs/{job.id}/reject").status_code == 409


def test_reject_all(client, db_session, episode_factory):
    for _ in range(3):
        _mk_approval_job(db_session, episode_factory)
    r = client.post("/api/v1/jobs/reject-all")
    assert r.status_code == 200
    assert r.json()["rejected"] == 3
```

Run — FAIL (404).

- [ ] **Step 2: Implement** in `jobs.py`, mirroring the existing `approve_job`/`approve_all` bodies exactly (same dependency style, same response shapes): reject sets `status=JobStatus.SKIPPED`, `reason="rejected in inbox"`, `finished_at=datetime.utcnow()`; 404 unknown id; 409 if `status != JobStatus.APPROVAL`; reject-all bulk-updates APPROVAL→SKIPPED and returns `{"rejected": n}`.

- [ ] **Step 3: Run** focused then full suite. **Step 4: Docs + commit** (`docs/modules/web.md` endpoint table). Commit `feat: reject endpoints for approval inbox`

---

### Task 5: UI shell — infosoud design (CSS + base.html)

Replace Pico CSS with a hand-written stylesheet adapted from infosoud_web; rewrite `base.html` with the gradient header + full nav. ALL existing pages must still render (they only use `{% block title %}`/`{% block content %}` plus plain tables/forms/buttons, which the new CSS styles at element level).

**Files:**
- Create: `audiobiblio/web/static/audiobiblio.css`
- Modify: `audiobiblio/web/templates/base.html` (full rewrite)
- Delete: `audiobiblio/web/static/style.css`
- Modify: `audiobiblio/web/views.py` (add `/_partials/inbox_badge` route)
- Create: `audiobiblio/web/templates/_partials/inbox_badge.html`

**Interfaces:**
- Produces: CSS classes all later tasks use: `.card`, `.grid-2`, `.grid-4`, `.badge` + `.badge-green/orange/red/gray`, `.btn`, `.btn-sm`, `.btn-outline`, `.btn-danger`, `.stat`, `.stat-num`, `.stat-label`, `.pill`, `.text-muted`, `.text-sm`, `.mt-1`, `.mb-1`. Nav `active` values: `home, inbox, targets, jobs, episodes, programs, ingest, catalog, logs`.

- [ ] **Step 1: Write `audiobiblio/web/static/audiobiblio.css`.** Take the infosoud_web `<style>` block VERBATIM as the base (it's in `~/projects/rejstriky/infosoud_web/templates/base.html` lines 7–228: reset, body, header, main, footer, .card, .error-box, badges, forms, .btn family, tables, links, .grid-2/.grid-4 + 600px breakpoint, .mt-1/.mb-1/.text-sm/.text-muted, print rules; the `.ac-*` autocomplete block may be dropped). Then append these audiobiblio-specific additions:

```css
/* ===== audiobiblio additions ===== */

/* Header nav (infosoud has only subtitle links; we need a real nav) */
header nav { display: flex; flex-wrap: wrap; gap: 0.2rem 1rem; margin-top: 0.3rem; }
header nav a { color: #cfe0f0; text-decoration: none; font-size: 0.9rem; padding: 0.1rem 0; }
header nav a:hover { color: #fff; }
header nav a.active { color: #fff; font-weight: 600; border-bottom: 2px solid #fff; }

/* Nav inbox counter */
.pill {
    display: inline-block; min-width: 1.3em; padding: 0 0.35em;
    border-radius: 999px; background: #f59e0b; color: #fff;
    font-size: 0.75rem; font-weight: 700; text-align: center;
}

/* Console stat cards */
.stat { text-align: center; padding: 1rem; }
.stat-num { font-size: 2rem; font-weight: 700; color: #1a3a5c; line-height: 1.1; }
.stat-num.stat-bad { color: #991b1b; }
.stat-label { font-size: 0.8rem; color: #6b7280; margin-top: 0.2rem; }
.stat-label a { color: inherit; }

/* Danger button (reject/delete) */
.btn-danger { background: #b91c1c; }
.btn-danger:hover { background: #7f1d1d; }
button { font: inherit; }  /* plain <button> in legacy templates */

/* Generic button element fallback so old pages stay usable */
button:not(.btn):not(.btn-sm) {
    padding: 0.3rem 0.7rem; background: #1e4d7b; color: #fff; border: none;
    border-radius: 5px; cursor: pointer; font-size: 0.85rem;
}

/* Legacy layout compat (old templates use .container / .container-fluid) */
.container, .container-fluid { max-width: 1000px; margin: 0 auto; padding: 0 1rem; width: 100%; }
```

- [ ] **Step 2: Rewrite `base.html`** (complete file):

```html
<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% block title %}audiobiblio{% endblock %}</title>
    <link rel="stylesheet" href="/static/audiobiblio.css">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://unpkg.com/htmx-ext-sse@2.2.2/sse.js"></script>
</head>
<body>
    <header>
        <div class="inner">
            <div>
                <h1><a href="/">audiobiblio</a></h1>
                <div class="subtitle">audioknihy pod kontrolou</div>
            </div>
            <nav>
                <a href="/" {% if active == 'home' %}class="active"{% endif %}>Console</a>
                <a href="/inbox" {% if active == 'inbox' %}class="active"{% endif %}>Inbox
                    <span hx-get="/_partials/inbox_badge" hx-trigger="load, every 30s" hx-swap="innerHTML"></span></a>
                <a href="/targets" {% if active == 'targets' %}class="active"{% endif %}>Sources</a>
                <a href="/jobs" {% if active == 'jobs' %}class="active"{% endif %}>Downloads</a>
                <a href="/episodes" {% if active == 'episodes' %}class="active"{% endif %}>Library</a>
                <a href="/programs" {% if active == 'programs' %}class="active"{% endif %}>Programs</a>
                <a href="/ingest" {% if active == 'ingest' %}class="active"{% endif %}>Ingest</a>
                <a href="/catalog" {% if active == 'catalog' %}class="active"{% endif %}>Catalog</a>
                <a href="/logs" {% if active == 'logs' %}class="active"{% endif %}>Logs</a>
            </nav>
        </div>
    </header>
    <main>
        {% block content %}{% endblock %}
    </main>
    <footer>
        <span hx-get="/_partials/stats" hx-trigger="load, every 30s" hx-swap="innerHTML" id="footer-stats"></span>
    </footer>
</body>
</html>
```

(The JDownloader page stays routable at `/jdownloader` but leaves the main nav — it's an integration detail, linked from the Downloads page in Task 9.)

- [ ] **Step 3: Inbox badge partial.** In `views.py` add route `GET /_partials/inbox_badge` → count `DownloadJob.status == JobStatus.APPROVAL` → render `_partials/inbox_badge.html`:

```html
{% if count %}<span class="pill">{{ count }}</span>{% endif %}
```

- [ ] **Step 4: Verify every page renders.** Start `uv run audiobiblio serve` in background; then:

```bash
for p in / /inbox /targets /jobs /episodes /programs /ingest /catalog /logs /jdownloader; do
  echo "$p $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080$p)"; done
```

Expect 200 for all existing routes (`/inbox` will 404 until Task 7 — that's the ONE allowed non-200 here). Visually spot-check `/` and `/jobs` (curl the HTML, confirm `audiobiblio.css` is referenced and no `pico` reference remains: `curl -s http://localhost:8080/ | grep -c pico` → 0). Kill the server.

- [ ] **Step 5: Suite + commit.** `uv run pytest -q` green. Update `docs/modules/web.md` (design language section: infosoud tokens, CSS file). Commit `feat: infosoud-style UI shell (vanilla CSS, gradient header, full nav)`

---

### Task 6: Console (home) rebuild

**Files:**
- Modify: `audiobiblio/web/views.py` (`index()` context)
- Modify: `audiobiblio/web/templates/index.html` (full rewrite)

**Interfaces:**
- Consumes: Task 5 CSS classes. Existing context vars keep their names; adds `running_jobs`, `error_jobs`, `disk_free_gb`, `targets_health`, `inbox_count`.

- [ ] **Step 1: Extend `index()`** — keep existing queries (`ep_total`, `ep_avail`, `j_pending`, `j_error`, `j_success`, `t_active`, `last_crawl`, `recent_jobs`) and add:

```python
import shutil

inbox_count = db.query(func.count(DownloadJob.id)).filter(
    DownloadJob.status == JobStatus.APPROVAL).scalar() or 0
running_jobs = db.query(DownloadJob).filter(
    DownloadJob.status == JobStatus.RUNNING).order_by(DownloadJob.started_at.desc()).limit(10).all()
error_jobs = db.query(DownloadJob).filter(
    DownloadJob.status == JobStatus.ERROR).order_by(DownloadJob.finished_at.desc()).limit(5).all()
targets_health = db.query(CrawlTarget).order_by(CrawlTarget.active.desc(),
    CrawlTarget.next_crawl_at.asc().nullslast()).limit(20).all()
try:
    usage = shutil.disk_usage(Path(cfg.library_dir).expanduser())
    disk_free_gb = round(usage.free / 1e9, 1)
except OSError:
    disk_free_gb = None
```

(`cfg = load_config()` — check how views.py currently accesses config; reuse that pattern rather than re-loading per request if a cached accessor exists.)

- [ ] **Step 2: Rewrite `index.html`:**

```html
{% extends "base.html" %}
{% block title %}Console — audiobiblio{% endblock %}
{% block content %}
<div class="grid-4">
    <div class="card stat">
        <div class="stat-num">{{ inbox_count }}</div>
        <div class="stat-label"><a href="/inbox">awaiting approval</a></div>
    </div>
    <div class="card stat">
        <div class="stat-num">{{ running_jobs|length }}</div>
        <div class="stat-label"><a href="/jobs?status=running">downloading</a></div>
    </div>
    <div class="card stat">
        <div class="stat-num {% if j_error %}stat-bad{% endif %}">{{ j_error }}</div>
        <div class="stat-label"><a href="/jobs?status=error">failed</a></div>
    </div>
    <div class="card stat">
        <div class="stat-num">{{ disk_free_gb if disk_free_gb is not none else "?" }}</div>
        <div class="stat-label">GB free (library)</div>
    </div>
</div>

{% if error_jobs %}
<div class="card">
    <h2>Recent failures</h2>
    <table>
        <tr><th>Episode</th><th>Error</th><th></th></tr>
        {% for j in error_jobs %}
        <tr>
            <td>{{ j.episode.title if j.episode else j.episode_id }}</td>
            <td class="text-sm text-muted">{{ (j.error or "")[:160] }}</td>
            <td><button class="btn btn-sm" hx-post="/api/v1/jobs/{{ j.id }}/retry"
                        hx-on::after-request="location.reload()">retry</button></td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

<div class="grid-2">
    <div class="card">
        <h2>Sources</h2>
        <table>
            <tr><th>Name</th><th>Mode</th><th>Last crawl</th><th></th></tr>
            {% for t in targets_health %}
            <tr>
                <td>{{ t.name or t.url }}</td>
                <td><span class="badge {% if t.approval_mode.value == 'auto' %}badge-green{% else %}badge-orange{% endif %}">{{ t.approval_mode.value }}</span></td>
                <td class="text-sm">{{ t.last_crawled_at.strftime('%d.%m. %H:%M') if t.last_crawled_at else "never" }}</td>
                <td><span class="badge {% if t.active %}badge-green{% else %}badge-gray{% endif %}">{{ "on" if t.active else "off" }}</span></td>
            </tr>
            {% endfor %}
        </table>
        <p class="mt-1"><a href="/targets">manage sources →</a></p>
    </div>
    <div class="card">
        <h2>Recent jobs</h2>
        <table>
            <tr><th>Episode</th><th>Status</th></tr>
            {% for j in recent_jobs %}
            <tr>
                <td>{{ j.episode.title if j.episode else j.episode_id }}</td>
                <td><span class="badge {% if j.status.value == 'success' %}badge-green{% elif j.status.value == 'error' %}badge-red{% elif j.status.value in ('pending','running') %}badge-orange{% else %}badge-gray{% endif %}">{{ j.status.value }}</span></td>
            </tr>
            {% endfor %}
        </table>
        <p class="mt-1"><a href="/jobs">all downloads →</a></p>
    </div>
</div>
{% endblock %}
```

(Adjust attribute access to reality: if `recent_jobs` rows are tuples or lack eager-loaded episodes, mirror what the current template does — check the existing `index.html` before deleting it.)

- [ ] **Step 3: Verify** — serve, `curl -s localhost:8080/ | grep -c "stat-num"` → 4; visual check. Suite green.
- [ ] **Step 4: Commit** `feat: console dashboard (inbox, downloads, failures, sources health, disk)`

---

### Task 7: Inbox page

**Files:**
- Modify: `audiobiblio/web/views.py` (add `/inbox` route)
- Create: `audiobiblio/web/templates/inbox.html`
- Test: `tests/web/test_inbox_view.py` (view renders with grouped jobs — use the Task 2 web conftest but include the views router + a template check)

**Interfaces:**
- Consumes: approve endpoints (existing), reject endpoints (Task 4), `build_paths_for_episode` (existing pattern from views.py:82-96).

- [ ] **Step 1: View.** Route `GET /inbox`, `active='inbox'`. Query all APPROVAL jobs with episode→work→series→program eager-loaded, compute `proposed_path` per job (reuse the existing try/except pattern from views.py lines 82–96), group into `groups: list[{program_name, target_hint, jobs}]` ordered by program name; totals in context.

- [ ] **Step 2: Template `inbox.html`:**

```html
{% extends "base.html" %}
{% block title %}Inbox — audiobiblio{% endblock %}
{% block content %}
<div class="card">
    <h2>Inbox — {{ total }} awaiting approval</h2>
    {% if total %}
    <p>
        <button class="btn" hx-post="/api/v1/jobs/approve-all" hx-confirm="Approve all {{ total }}?"
                hx-on::after-request="location.reload()">Approve all</button>
        <button class="btn btn-danger btn-sm" hx-post="/api/v1/jobs/reject-all" hx-confirm="Reject all {{ total }}?"
                hx-on::after-request="location.reload()">Reject all</button>
    </p>
    {% else %}<p class="text-muted">Nothing to review. 🎉</p>{% endif %}
</div>

{% for g in groups %}
<div class="card">
    <h2>{{ g.program_name }} <span class="text-sm text-muted">({{ g.jobs|length }})</span></h2>
    <table>
        <tr><th>Episode</th><th>Proposed path</th><th style="width:12rem"></th></tr>
        {% for j in g.jobs %}
        <tr id="inbox-row-{{ j.id }}">
            <td>{{ j.episode.title if j.episode else j.episode_id }}
                {% if j.episode and j.episode.url %}<br><a class="text-sm" href="{{ j.episode.url }}" target="_blank">source ↗</a>{% endif %}</td>
            <td class="text-sm text-muted">{{ j.proposed_path }}</td>
            <td>
                <button class="btn btn-sm" hx-post="/api/v1/jobs/{{ j.id }}/approve"
                        hx-target="#inbox-row-{{ j.id }}" hx-swap="delete">approve</button>
                <button class="btn btn-sm btn-danger" hx-post="/api/v1/jobs/{{ j.id }}/reject"
                        hx-target="#inbox-row-{{ j.id }}" hx-swap="delete">reject</button>
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 3: View test** (`tests/web/test_inbox_view.py`) — extend the web conftest app with the views router and templates; create 2 APPROVAL jobs in one program via factories; GET `/inbox`; assert 200 and both episode titles in the body. (If mounting the full views router in the test app drags in config/scheduler dependencies, test the grouping logic as a pure function instead — extract `def _group_approval_jobs(db) -> ...` in views.py and unit-test that; note which route you took in the report.)

- [ ] **Step 4: Verify live** — serve; if no real APPROVAL jobs exist in the dev DB, note that and rely on the test; else click through approve/reject on one row. Suite + lint green.

- [ ] **Step 5: Docs + commit.** `docs/modules/web.md` pages table + `docs/workflows.md` §4.1 step 4 → `[works today]`. Commit `feat: inbox page — grouped approval queue with approve/reject`

---

### Task 8: Sources page rebuild

**Files:**
- Modify: `audiobiblio/web/views.py` (`targets_page` context: add per-target last-run info)
- Modify: `audiobiblio/web/templates/targets.html` (full rewrite)

**Interfaces:**
- Consumes: targets API incl. `approval_mode` (Task 2), `POST /api/v1/targets/{id}/crawl` (existing).

- [ ] **Step 1: Rewrite `targets.html`** — card 1: add-source form (fields: url [text], kind [select station/program/series], name [text, optional], interval_hours [number, default 24], approval_mode [select review (default)/auto]) submitting via HTMX `hx-post="/api/v1/targets" hx-ext="json-enc"` — check whether json-enc is loaded; if not, add a 10-line inline JS submit handler that builds the JSON body via fetch() and reloads (keep it dependency-free). Card 2: sources table — name/url, kind badge, mode badge with inline toggle (`hx-patch` … `{"approval_mode": "auto"}` / `"review"`), interval, last_crawled_at, next_crawl_at, active toggle (`hx-patch` `{"active": …}`), "crawl now" button (`hx-post /api/v1/targets/{{t.id}}/crawl`), delete button (`hx-delete`, `hx-confirm`). All actions `hx-on::after-request="location.reload()"`.

- [ ] **Step 2: Verify live** — serve; add a real target (e.g. a mujrozhlas program you follow) with mode review; toggle mode; hit crawl-now; confirm the crawl runs (watch `/logs` or server log) and discovered jobs appear in `/inbox`. **This is the first real end-to-end of the daily loop — describe the outcome in the report.**

- [ ] **Step 3: Suite + docs + commit** `feat: sources page — add/edit targets with approval-mode and crawl-now`

---

### Task 9: Downloads page rebuild + SSE live refresh

**Files:**
- Modify: `audiobiblio/web/templates/jobs.html` (full rewrite in new design)
- Modify: `audiobiblio/web/views.py` (`jobs_page`: add WATCH section context `watch_jobs`)
- Modify: `audiobiblio/web/templates/_partials/job_rows.html` (badge styling to match)

**Interfaces:**
- Consumes: existing jobs API (list/retry/retry-all), SSE endpoint `GET /api/v1/events` (existing, publishes `run_jobs_completed` etc. via TaskTracker), `_partials/job_rows` route (existing).

- [ ] **Step 1: Rewrite `jobs.html`:** status filter tabs (all/pending/running/success/error/watch/skipped — links with `?status=`), jobs table rendered via the existing `_partials/job_rows.html` include, pagination (keep current `page`/`pages` vars), "Retry all failed" button, WATCH card listing `watch_jobs` (episodes gone-but-monitored) with a note about the availability checker, link to `/jdownloader`. Approval queue section moves OUT of this page (now the Inbox) — remove it, keep a link to `/inbox` when `approval_count > 0`.

- [ ] **Step 2: SSE refresh.** Wrap the jobs table in:

```html
<div hx-ext="sse" sse-connect="/api/v1/events" sse-swap="message"
     hx-get="/_partials/job_rows?status={{ status_filter or '' }}&page={{ page }}"
     hx-trigger="sse:message" hx-target="#job-rows" hx-swap="innerHTML">
    <tbody id="job-rows">…</tbody>
</div>
```

Check the htmx-sse extension semantics against the actual event stream format in `web/routers/sse.py` (events are JSON strings, no SSE `event:` name field → they arrive as `message`). If `sse-swap="message"` conflicts with using `hx-trigger="sse:message"`, use only the trigger form. Verify in browser (or curl the SSE endpoint + trigger a crawl) that a completed background task refreshes the rows; if the stream stays silent in your test window, verify the 30s footer poll still updates and note it.

- [ ] **Step 3: Badges in `_partials/job_rows.html`** — same badge mapping as Console (success→green, error→red, pending/running→orange, watch→orange, else gray).

- [ ] **Step 4: Suite + verify all routes 200 + docs + commit** `feat: downloads page — filters, watch list, SSE-refreshed rows`

---

### Task 10: Phase 2 verification gate + deploy prep

**Files:** none new (report + small doc fixes only)

- [ ] **Step 1:** `uv run pytest -q && uv run lint-imports` — green, KEPT.
- [ ] **Step 2:** Route census: serve, loop all routes (incl. `/inbox`), expect all 200; `grep -c pico` on `/` → 0.
- [ ] **Step 3:** Migration state: `uv run alembic current` shows the approval_mode revision as head; `downgrade -1 && upgrade head` clean.
- [ ] **Step 4:** End-to-end on real data (read-mostly): one review-mode target crawled → jobs in Inbox → approve one → it downloads → file lands in `download_dir`, tags written, job SUCCESS. One auto-mode target → jobs go straight to PENDING. Document both runs' outcomes. (Downloads hit rozhlas.cz — keep it to 1–2 episodes, rate limit stays 0.5 rps.)
- [ ] **Step 5:** Docs sweep: `docs/workflows.md` §4.1 all steps re-marked; `docs/modules/web.md` current. Report: test counts, route census, e2e outcomes, anything discovered for Phase 3.

---

## Later Phases (unchanged from Phase 1 plan outline)

Phase 3 quality & upgrades · Phase 4 sync & import · Phase 5 enrichment & gaps · Phase 6 polish. Each gets its own plan when its predecessor lands.
