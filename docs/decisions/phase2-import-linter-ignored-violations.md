# Parked import-linter violations (phase2 backlog)

Recorded as of Task 6 (import-linter contract). These imports violate the
layered architecture contract but are parked with `ignore_imports` because
untangling them mechanically would require a larger refactor than is in scope
for Phase 1.

## acquire -> library (same-tier coupling)

**Files:** `audiobiblio/acquire/{downloader,crawler}.py`

**Imports flagged:**

- `audiobiblio.acquire.downloader -> audiobiblio.library.pipelines.postprocess`
- `audiobiblio.acquire.downloader -> audiobiblio.library.pipelines.library`
- `audiobiblio.acquire.crawler -> audiobiblio.library.pipelines.ingest`

**Why:** `acquire` (downloader, crawler) calls library
pipeline functions (`tag_audio`, `build_paths_for_episode`,
`upsert_from_item`) at runtime to process content after download. These are not
type-hint-only references and the helpers live in `library.pipelines` because
they are also called from `web` and `cli`.

**Note:** `audiobiblio.acquire.availability -> audiobiblio.library.pipelines.checks`
was initially parked here but turned out to be a dead import (`plan_downloads` was
never used in `availability.py`). It has been removed (see commit fixing dead import).

**Phase 2 plan:** Introduce a shared post-download callback protocol (e.g.
`core.ports.PostDownloadHook`) or event bus so `acquire` can fire events that
`library` subscribes to, removing the direct cross-tier call.

---

## cli -> web (upward import)

**File:** `audiobiblio/cli.py`

**Import flagged:** `audiobiblio.cli -> audiobiblio.web.app`

**Why:** The `serve_web` command in `cli.py` (line 679) bootstraps the FastAPI
application inline with a deferred `from .web.app import create_app` inside the
function body. Structurally, `cli` and `web` should be peers at the same tier,
but the contract orders `web` above `cli` to reflect that the web layer is the
primary runtime entry point. The CLI's `serve_web` command is a convenience
wrapper that drives the web layer, so the dependency flows upward.

**Phase 2 plan:** Extract `serve_web` to a dedicated entry point module (e.g.
`audiobiblio/web/__main__.py` or a separate `audiobiblio/entrypoints/serve.py`)
so that `cli.py` no longer needs to import from `web`.
