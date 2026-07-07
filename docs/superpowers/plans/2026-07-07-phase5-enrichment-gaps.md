# Phase 5: Enrichment, Gaps & NAS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal (user priorities, in order):** (1) existing downloaded data becomes properly titled/described by finally READING the meta_json files we already download; (2) the system moves to always-on NAS operation so source monitoring stops slipping (scheduler currently runs only while the Mac serves); (3) series completeness becomes visible and missing episodes hunted; plus the paste-URL→series flow, databazeknih enrichment, and optional finalize-complete-work grouping.

**Architecture:** Spec §4.4 + §4.5. Enrichment writes through the Phase 4 provenance stack (SCRAPED source="meta_json", ENRICHED source="databazeknih"; guards: `is_generic_title`, `has_manual`, shared `WORK_FIELDS` routing). Grounded facts: `.info.json` lands as `{program_dir}/{stem}.info.json` with ZERO consumers today; Dockerfile HEALTHCHECK hits `/health` but the route is `/api/v1/health` (broken in-container health); seed creates 98 Programs but no CrawlTargets; `Episode.episode_number` is unreliable (index fallback); layout is flat per-program (user rule: keep flat; never move without explicit approval).

**Tech Stack:** as Phase 4 + requests/BS4 for databazeknih (fixture-tested), Docker for deploy prep.

## Global Constraints

- Migrations chain from head `eb491e6892f5`, single chain, drift stripped + documented.
- **User rules (binding):** manual (MANUAL) values never overwritten; generic titles never become titles/tags/filenames; folder structure stays FLAT by default — per-work folders are created ONLY by the explicit finalize action per work; files never deleted (trash only); dense UI; plain track numbers.
- All enrichment writers go through `record_value` + guards; ORM updates consult `has_manual` and the richer-title rule where applicable.
- Suite green (`uv run pytest -q`, currently 294), `lint-imports` KEPT. Docs part of done. Conventional commits, no AI attribution. Branch `feature/phase5-enrichment-gaps` off main.
- Live checks on port 8090; real-DB mutations only where a task says so (dry-run first). Rate limits: mujrozhlas 0.5 rps stays; databazeknih max 1 req/2 s + honest User-Agent.

---

### Task 1: Meta_json enrichment — read back what we already downloaded

The user's "Episode 9" rows have real titles sitting in `.info.json` on disk ("Karel Horký: Nad mrtvým netopýrem…" verified). Zero consumers exist today.

**Files:** Create `audiobiblio/library/enrich_meta.py`; modify `audiobiblio/acquire/downloader.py` (hook after `_download_meta_json` success, isolated try/except like the mediainfo hook); modify `audiobiblio/cli.py` (`enrich-from-meta` command); test `tests/library/test_enrich_meta.py`.

**Interfaces:**
- `enrich_episode_from_meta(session, episode) -> EnrichReport` (frozen dataclass: `fields_updated: tuple[str, ...]`, `skipped: tuple[str, ...]`, `note: str`). Locates the episode's COMPLETE META_JSON asset; tolerant `json.load` (malformed → note, never raises). Extracts: `title`/`fulltitle`, `description`, `series`, `episode`/`track` numbers, `duration` (s→ms).
- Per-field rules: title — skip if `is_generic_title(candidate)`; skip if `has_manual(episode, "title")`; update ORM when current title matches the fallback pattern `^Episode \d+$` OR candidate is longer than current (the ingest richer-title rule); ALWAYS `record_value(SCRAPED, source="meta_json")` for surviving candidates. description → `episode.summary` set-only-when-empty + provenance. `episode.duration_ms` only-if-NULL. `episode_number` only-if-NULL and source provides a real number.
- CLI: `enrich-from-meta [--limit N] [--dry-run]` sweeping episodes that have a COMPLETE META_JSON asset (order: fallback-titled first — `title LIKE 'Episode %'`).
- Downloader hook: after meta_json download success, call enrich (isolated; failure logs warning, never fails the job).

- [ ] TDD: real-fixture test (write a small info.json into tmp_path with title/description/duration), fallback-title update, generic candidate skipped, MANUAL protected, malformed JSON tolerated, dry-run pure, provenance rows recorded. RED → GREEN.
- [ ] Suite + lint. Docs: library.md + workflows.md §4.4 marker `[partial: meta_json live]`.
- [ ] **Real-data run (this is the user's #1 ask):** `enrich-from-meta --dry-run` quote counts, then real run; verify the 13 formerly-generic episodes got real titles (quote 3 before/after examples). Do NOT sync-tags --write here (gate does a controlled sample).
- [ ] Commit `feat: enrich episodes from downloaded meta_json (backfill + download hook)`

---

### Task 2: NAS deployment prep — image, healthcheck, deploy guide

The scheduler only runs while `serve` runs — the root cause of "sources slipping". Target: everything ready for a supervised deploy to nasx (the actual deploy happens with the user post-merge).

**Files:** Modify `Dockerfile` (add `exiftool` to apt-get; FIX HEALTHCHECK path — verify actual route first: system router serves `/api/v1/health`); modify `docker-compose.yml` (env: `AUDIOBIBLIO_INBOX_DIRS` example commented, healthcheck alignment); create `docs/deploy-nas.md`; test: none (build verification instead).

**Steps:**
- [ ] Fix HEALTHCHECK to the real path (`curl -f http://localhost:8080/api/v1/health`). Add `exiftool` to the apt-get install line (sync engine M4A reads on NAS).
- [ ] `docs/deploy-nas.md` — concrete, ordered: build image (on NAS or `docker buildx` + save/load), volume mapping (`MEDIA_PATH=/volume3/eBOOKs/audiobooks`), **DB carry-over**: copy the local dev `db.sqlite3` (all curation lives there) into the compose data volume BEFORE first start (exact `docker cp`/volume-path commands with placeholders), first-start checklist (alembic auto-upgrades via entrypoint; verify `/api/v1/health`; verify targets crawl), inbox-dir pattern for laptop→NAS handoff (Synology Drive/rsync folder listed in `AUDIOBIBLIO_INBOX_DIRS`), and the eBOOKs first-scan advice (read-only: scan creates findings only; accept nothing until reviewed).
- [ ] Verification: `docker build -t audiobiblio-test .` (if the daemon runs; else `uv pip install -e . --dry-run` fallback + note), then if built: `docker run --rm -d -p 18080:8080 audiobiblio-test` → healthcheck goes healthy → kill. Quote outcomes.
- [ ] Suite + lint (unchanged code paths — confirm). Docs: workflows.md note that always-on monitoring requires the NAS deploy. Commit `feat: NAS deploy prep — exiftool in image, fixed healthcheck, deploy guide`

---

### Task 3: Source freshness — overdue visibility

**Files:** Modify `audiobiblio/web/views.py` (index: overdue computation), `index.html` (badge on Sources card rows + counter line), `audiobiblio/cli.py` (`crawl-status` command); test extend `tests/web/test_inbox_view.py`-style context test + CLI logic test.

**Interfaces:** a target is `overdue` when `active AND next_crawl_at < now - 0.5*interval_hours` (grace 50%); Console sources table gets a red `overdue` badge + "N sources overdue" counter line under the failed stat when >0; `crawl-status` prints per-target: name, last, next, state (ok/due/overdue/inactive). Pure helper `target_state(target, now) -> str` in `acquire/crawler.py` or views — unit-tested with explicit `now`.

- [ ] TDD (explicit now, no wall-clock): ok/due/overdue/inactive cases. Implement + wire. Suite + lint. Docs: web.md. Commit `feat: source freshness — overdue badges and crawl-status`

---

### Task 4: Completeness — expected totals, gap report, gap-fill priority

**Files:** Modify `core/db/models.py` (Work: `expected_total: Optional[int]`, `expected_source: Optional[String(50)]`); migration; modify `web/routers/episodes.py` or new `works.py` router (PATCH /api/v1/works/{id} {"expected_total": N} — MANUAL provenance row `("work", id, "expected_total")` + ORM); create `audiobiblio/library/gaps2.py` (or extend `library/pipelines/gaps.py` — read it first; keep one home, note decision): `work_completeness(session, work) -> Completeness(have: int, expected: int | None, missing_numbers: list[int] | None)` — have = episodes with COMPLETE audio; missing_numbers only when episode numbering looks dense/trustworthy (≥80% of episodes have distinct numbers — document heuristic); `incomplete_works(session, limit)` sorted by closeness. View `/gaps` (nav under Library or a Library-page section — pick, justify): dense table work/program/have/expected/missing/link. Console "gaps" counter. **Gap-fill priority:** in crawler ingest path, when a NEW candidate episode belongs to a work with `expected_total` set and `have < expected`, set `episode.priority = 10` and the job reason gains "gap-fill" (shows in Inbox) — modest, no cross-source hunting engine yet (deferred, recorded).

- [ ] TDD: completeness math (dense/sparse numbering), PATCH work, incomplete_works ordering, gap-fill priority on ingest. Migration cycle. Suite + lint. Docs: workflows §4.5 markers (mark cross-source hunting `[deferred: phase 5+]` honestly). Commit `feat: work completeness — expected totals, gap report, gap-fill priority`

---

### Task 5: Paste-URL → whole series flow

**Files:** Modify `sources/mrz_inspector.py` (`parent_url(url: str) -> str | None` — from `_mrz_parts`, episode URL → program URL, None when already top-level); modify `web/routers/ingest.py` (preview response gains `kind` + `parent` block: when the pasted URL classifies as EPISODE, probe the parent too and return `{parent_url, parent_title, parent_episode_count}`); modify `ingest.html` (when parent present: buttons **[Přidat jen epizodu]** / **[Přidat celý pořad jako zdroj]** with review/auto select → creates CrawlTarget + fires crawl-now via existing endpoints); test router + parent_url units.

- [ ] TDD: parent_url derivation cases (episode→program, program→None, non-mrz→None); preview envelope with parent; target-creation path (reuses targets API — test the ingest endpoint orchestration only). Suite + lint + route census. Docs: web.md + workflows §4.1. Commit `feat: paste episode URL — offer whole-program target with backfill`

---

### Task 6: databazeknih enrichment (greenfield)

**Files:** Create `audiobiblio/sources/databazeknih.py`; modify `web/routers/episodes.py` or works router (POST /api/v1/works/{id}/enrich → task_tracker); `episode_detail.html` (Re-enrich button in the metadata card); tests `tests/sources/test_databazeknih.py` with SAVED HTML fixtures.

**Interfaces:**
- `search_book(title: str, author: str | None) -> list[DbkHit(url, title, author)]` and `fetch_book(url) -> DbkBook(title, author, year, description, genres: list[str], narrator: str | None, cover_url: str | None)` — requests + BS4, rate limiter (1 req/2 s, module-level), honest UA "audiobiblio/0.5 (personal audiobook manager)". Both never raise on HTTP/parse errors (return []/None + warning).
- `enrich_work_from_dbk(session, work) -> EnrichReport` — best hit by fuzzy title+author (SequenceMatcher > 0.85 else skip with note "ambiguous"); record ENRICHED provenance (source="databazeknih") for year/description/genres/narrator; ORM: work.year set-only-when-empty + has_manual guard; genres → provenance only (episode-level genre field per WORK_FIELDS routing — record on each episode? NO: record on work? genre routes to EPISODE per Phase 4 decision — record ENRICHED genre per episode of the work; document). Cache raw result in `work.extra["dbk"]` (dict reassignment).
- **Fixture policy:** implementer fetches ONE real search page + ONE book page politely (2 requests total), saves under tests/fixtures/dbk/, tests parse those; live fetch behind `@pytest.mark.skip` unless RUN_LIVE env.

- [ ] TDD on fixtures: parse search hits, parse book fields, ambiguous-skip, provenance+guards, cache write. Suite + lint (sources imports core only ✓). Docs: new `docs/modules/sources.md` section + workflows §4.4 → `[works today: meta_json + databazeknih on demand]`. Commit `feat: databazeknih enrichment with ENRICHED provenance`

---

### Task 7: Finalize complete work — optional per-work folder (explicit action only)

**Files:** Create `audiobiblio/library/finalize.py` (`plan_finalize(session, work, library_dir) -> list[Action]` + `finalize_work(...) -> report` — moves the work's COMPLETE audio (+sidecar meta/webpage) files into `{program_dir}/{Author} - ({year}) {Album}/`, updates Asset.file_path, records nothing else; dry list first; collision suffixes; never touches other works' files); works router POST /api/v1/works/{id}/finalize {"dry_run": true}; detail page / gaps page: [Finalize] button shown ONLY when completeness says have==expected (Task 4); tests with tmp files.

**Binding:** flat-by-default stands — this NEVER runs automatically; one work per explicit user click; preview shown first (dry-run list in a details element, same pattern as dedupe merge preview).

- [ ] TDD: plan actions correct, finalize moves+updates assets, collision, refuses when expected unset or incomplete (409 at router), files never deleted. Suite + lint + census. Docs: library.md + workflows (rename-after-complete → works today, manual). Commit `feat: finalize complete work into per-work folder (explicit, previewed)`

---

### Task 8: Phase 5 verification gate

- [ ] Suite + lint + `alembic heads` (one) + cycle for this phase's migration.
- [ ] Route census (incl. /gaps) on 8090.
- [ ] **Real data:** quote enrich-from-meta results (already run in T1 — re-verify 3 titles); `sync-tags --episode-id <one enriched SFT ep> --write` → mutagen read-back shows the real title in ©nam (the full circle: meta_json → DB → file tag). `crawl-status` on the real targets — quote overdue states. Set `expected_total` on the SFT work via API (the user knows the series has a known episode count — use a plausible value, note it's user-adjustable), quote the gap report. Paste-URL flow live: use a real mujrozhlas EPISODE url (pick from DB aliases), verify the parent offer appears (do NOT add the target unless it's one of the 4 existing). databazeknih: enrich ONE real work (2 polite requests), quote what it found.
- [ ] Docker build + healthcheck (if daemon available).
- [ ] Docs sweep + CHANGELOG Unreleased→(hold for merge). Report.

---

## Deferred (recorded)

- Cross-source gap HUNTING engine (fuzzy-matching every discovery against all wanted lists) — gap-fill priority ships now; the hunt engine needs design (candidate volume) → Phase 5+/6.
- ABS push absorption, System page, auth — Phase 6.
- Actual nasx deploy — done WITH the user right after this phase merges (docs/deploy-nas.md is the script).
- CrawlTarget seeding from the 98-program list (user adds targets deliberately; a "add target from program" shortcut exists via paste-URL flow).
