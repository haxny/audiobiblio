# acquire — Crawl scheduling, download queue, and yt-dlp execution

**Layer:** Layer 3 of 5 (same tier as `library`). May import from `core`, `sources`, `dedupe`, and `tags`. Has four parked same-tier imports to `library` pipelines (see [decisions/phase2-import-linter-ignored-violations.md](../decisions/phase2-import-linter-ignored-violations.md)).
**Standalone use:**
- `uv run audiobiblio run-jobs` — execute pending DownloadJobs
- `uv run audiobiblio scheduler` — start blocking scheduler daemon (crawl + download)
- `uv run audiobiblio target-add --url URL --kind program` — register a crawl target
- `uv run audiobiblio target-list` — list all crawl targets
- `uv run audiobiblio target-toggle <id>` — enable/disable a target
- `uv run audiobiblio crawl-url --url URL` — one-shot crawl of a program/series URL
- `uv run audiobiblio crawl-status` — dense Rich table of all crawl targets with freshness state (ok / due / overdue / inactive)

## Responsibilities

- Maintains the `CrawlTarget` table (URL + kind + active flag + interval in hours); crawls targets on schedule.
- Runs yt-dlp to download audio (`m4a`), info JSON, and the episode webpage; updates `Asset` and `DownloadJob` rows.
- After a successful audio download, calls `tags.tag_audio()` to write metadata tags before recording the asset as `COMPLETE`.
- Runs APScheduler with three periodic jobs: crawl due targets (default 60 min), execute pending downloads (default 5 min), check availability (every 6 h).
- Probes episode URLs for availability (`AVAILABLE / UNAVAILABLE / GONE`); re-queues `WATCH`-status jobs when a previously gone episode reappears.
- Provides a JDownloader integration path for sources that require it.

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `run_pending_jobs` | `(limit=None) -> int` | Execute PENDING DownloadJobs; returns number run |
| `crawl_target` | `(target: CrawlTarget, session=None) -> int` | Crawl one target; threads `target.approval_mode` into download planning; returns new jobs queued |
| `run_due_crawls` | `() -> int` | Crawl all due targets; returns total jobs queued |
| `target_state` | `(target, now: datetime) -> str` | Pure freshness classifier — returns `"inactive" \| "ok" \| "due" \| "overdue"` (see below) |
| `create_scheduler` | `(crawl_interval_minutes, download_interval_minutes) -> BackgroundScheduler` | Build APScheduler instance (does not start it) |
| `start_scheduler` | `(crawl_interval_minutes, download_interval_minutes)` | Blocking scheduler for CLI daemon mode |
| `check_unknown_episodes` | `(limit=50) -> int` | Probe episodes with UNKNOWN availability |
| `process_watch_list` | `() -> int` | Re-check WATCH-status jobs; re-queue if URL live |

### `target_state(target, now)` state machine

```
not target.active            → "inactive"
next_crawl_at is None        → "due"          (never been crawled)
next_crawl_at > now          → "ok"
next_crawl_at < now − 0.5×interval_hours → "overdue"  (missed by >50% of interval)
otherwise                    → "due"           (slightly past due, within grace window)
```

Used by `views.index()` (overdue badge on source rows, overdue counter under failed-jobs stat) and by the `crawl-status` CLI command.

## Files

| File | Purpose |
|---|---|
| `crawler.py` | `crawl_target()`, `run_due_crawls()`; discovers episodes and upserts via `library.pipelines.ingest` |
| `downloader.py` | `run_pending_jobs()`; yt-dlp subprocess execution for AUDIO, META_JSON, WEBPAGE assets |
| `scheduler.py` | `create_scheduler()`, `start_scheduler()`; APScheduler wiring |
| `availability.py` | `check_unknown_episodes()`, `process_watch_list()`; HTTP probes + WATCH re-queue |
| `jdownloader.py` | JDownloader MyJDownloader API client (fallback downloader) |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 2:** Per-`CrawlTarget` auto-download vs review-first switch (currently implemented at the program level via approval threshold in `library.pipelines.checks`).
- **Phase 2:** Circuit breakers per source: repeated failures pause a target and show a badge on the Console.
- **Phase 2:** Live download progress reporting via SSE.
- **Phase 3:** Quality comparison on re-discovered episodes (bitrate/container/duration); auto-upgrade when clearly better; ad-suspect detection when durations diverge beyond tolerance.
- **Phase 2:** Decouple `acquire → library` via `core.ports` event bus (resolves parked import-linter violations).
