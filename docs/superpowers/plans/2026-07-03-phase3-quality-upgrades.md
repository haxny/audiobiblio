# Phase 3: Quality & Upgrades — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Downloads get correct, complete tags; every audio asset knows its measurable quality; re-airs are detected and evaluated (auto-upgrade never triggers on ads); replaced files go to a 30-day trash with tag carry-over; a Dedupe page surfaces duplicate clusters. Plus the Phase 2 gate's bug findings fixed.

**Architecture:** Extends spec §4.2. Upgrade detection hooks the existing re-air matcher in `library/pipelines/ingest.py:_find_existing_episode` (url_reair path). Remote probes give duration only (yt-dlp `-J`), so pre-download decisions use duration; bitrate comparison happens after staging a candidate. New table `upgrade_candidates`; ad-suspect pairs surface in the Inbox. `AssetStatus.STALE` (defined, never set) finally gets used.

**Tech Stack:** Python ≥3.10, mutagen (media info + tags), SQLAlchemy/Alembic, FastAPI/Jinja2/HTMX, pytest, uv.

## Global Constraints

- New Alembic migrations chain from head `20f737dc3b98` (single chain — verify `uv run alembic heads` before each autogenerate; strip drift noise ops, document in-file).
- **User rules (binding):** plain track numbers — never "N of Total" (ID3 TRCK `"16"`, MP4 `trkn=(16, 0)`); metadata DB is source of truth; manual edits never overwritten automatically; directory names on disk never modified; deleted/replaced files ALWAYS go to trash first, never unlink directly.
- Ad rule (spec §4.2): duration difference > 5 seconds is NEVER auto-resolved — always a review pair. Shorter-but-clean beats longer-with-ads; the human decides until fingerprint heuristics exist (out of scope this phase).
- Suite green (`uv run pytest -q`, currently 72), `uv run lint-imports` KEPT (no new violations; dedupe/library layer may import core only; web on top).
- Docs are part of done (modules pages + workflows.md markers in the same commit).
- Commits `<type>: <description>`, no AI attribution. New branch `feature/phase3-quality-upgrades` off main.
- Real-data operations in tasks are read-mostly; any download is limited to 1–2 episodes (rate limit 0.5 rps stays).

---

### Task 1: Per-episode approval — approve/reject cascade to sibling jobs

Gate finding: approving the AUDIO job leaves the episode's META_JSON/WEBPAGE jobs in APPROVAL forever (assets stay MISSING). Approval decisions are per-EPISODE, not per-job.

**Files:**
- Modify: `audiobiblio/web/routers/jobs.py` (approve_job, reject_job cascade)
- Modify: `audiobiblio/web/views.py` (`_group_approval_jobs` → one row per episode)
- Modify: `audiobiblio/web/templates/inbox.html` (row = episode; show asset-type count)
- Test: extend `tests/web/test_jobs_api.py`, `tests/web/test_inbox_view.py`

**Interfaces:**
- Produces: `POST /api/v1/jobs/{job_id}/approve` and `/reject` now flip ALL of that job's episode's APPROVAL jobs (same `episode_id`, status APPROVAL) in one transaction; response gains `{"cascaded": n}`. Inbox groups return one entry per episode with `job_ids: list[int]` and `asset_types: list[str]`.

- [ ] **Step 1: Failing tests** — append to `tests/web/test_jobs_api.py`:

```python
def _mk_episode_jobs(db_session, episode_factory, statuses=("APPROVAL",) * 3):
    from audiobiblio.core.db.models import AssetType
    ep = episode_factory()
    types = [AssetType.AUDIO, AssetType.META_JSON, AssetType.WEBPAGE]
    jobs = []
    for t, st in zip(types, statuses):
        j = DownloadJob(episode_id=ep.id, asset_type=t, status=JobStatus[st])
        db_session.add(j)
        jobs.append(j)
    db_session.flush()
    return ep, jobs


def test_approve_cascades_to_sibling_jobs(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{jobs[0].id}/approve")
    assert r.status_code == 200
    assert r.json()["cascaded"] == 3
    for j in jobs:
        db_session.expire(j)
        assert j.status == JobStatus.PENDING


def test_reject_cascades_to_sibling_jobs(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory)
    r = client.post(f"/api/v1/jobs/{jobs[1].id}/reject")
    assert r.status_code == 200
    assert r.json()["cascaded"] == 3
    for j in jobs:
        db_session.expire(j)
        assert j.status == JobStatus.SKIPPED


def test_cascade_skips_non_approval_siblings(client, db_session, episode_factory):
    ep, jobs = _mk_episode_jobs(db_session, episode_factory,
                                statuses=("APPROVAL", "SUCCESS", "APPROVAL"))
    r = client.post(f"/api/v1/jobs/{jobs[0].id}/approve")
    assert r.json()["cascaded"] == 2
    db_session.expire(jobs[1])
    assert jobs[1].status == JobStatus.SUCCESS  # untouched
```

Run: FAIL (no `cascaded` key; siblings untouched).

- [ ] **Step 2: Implement cascade** in both endpoints: after validating the addressed job is APPROVAL (409 otherwise, unchanged), bulk-update `DownloadJob.episode_id == job.episode_id AND status == APPROVAL` → PENDING (approve) / SKIPPED+reason+finished_at (reject); return count as `cascaded`. Keep 404/409 semantics for the addressed job.

- [ ] **Step 3: Inbox per-episode rows.** `_group_approval_jobs`: group jobs by `episode_id` first (one dict per episode: title, proposed_path from the AUDIO job if present else first job, `job_ids`, `asset_types`), then group episodes by program. Template row shows episode title + small `text-muted` asset summary ("audio + meta + web"), approve/reject buttons target ANY one job id (cascade handles the rest), `hx-target` deletes the episode row. Update the inbox-view tests for the new shape.

- [ ] **Step 4:** `uv run pytest -q` green; lint KEPT. Docs: `docs/modules/web.md` (cascade semantics), `docs/workflows.md` §4.1. Commit `fix: approve/reject cascade to all sibling jobs of the episode`

---

### Task 2: Tag correctness — plain track numbers + title always written

Gate file evidence: `trkn=(16, 3)` (episode 16, work has only 3 episodes in DB — the total lies on incomplete works) and empty `©nam`. User rule: plain track numbers, no totals. Title: chapter/episode title should be written whenever it exists (see docs/modules/tags/ chapter-title-preservation note).

**Files:**
- Modify: `audiobiblio/library/pipelines/postprocess.py` (`tag_audio` lines ~207–262: tracknumber + track_title logic)
- Modify: `audiobiblio/tags/writer.py` only if MP4 trkn parsing requires it (check `_write_mp4` handling of `"16"` vs `"16 of 3"`)
- Test: `tests/library/test_tag_audio.py` (new)

**Interfaces:**
- Produces: `tag_audio` writes `tracknumber = str(ep.episode_number)` when set (no total, ever); `track_title = _u(ep_title)` whenever `ep_title` is non-empty AND differs from the album title (single-file works where episode title == work title keep title = album per naming pattern 1). `_count_episodes_in_work` no longer feeds the track tag (keep the function; it may serve display elsewhere).

- [ ] **Step 1: Read the current code first** — `postprocess.py` lines 180–265 and `writer.py` `_write_mp4` trkn handling. Confirm how `"16 of 3"` becomes `(16, 3)` and what `is_anthology` is (document it in the test file docstring — it's why the gate file lost its title).

- [ ] **Step 2: Failing tests** — `tests/library/test_tag_audio.py`. Build the album/track tag dicts through `tag_audio`'s pure assembly if extractable; otherwise test through a real tiny m4a fixture: create a 1-second silent m4a in tmp_path (`uv run python -c` ffmpeg via yt-dlp's bundled... simpler: commit a 5KB silent fixture `tests/fixtures/silent.m4a` generated once with ffmpeg — generate it in the test session via `subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "anullsrc", "-t", "0.3", ...])` and `pytest.skip` if ffmpeg is absent). Cases:

```python
def test_plain_track_number_no_total(...):
    # ep.episode_number=16, work with 3 episodes -> MP4 trkn == [(16, 0)]

def test_title_written_when_differs_from_album(...):
    # ep.title = "Kapitola 2", work.title = "Kniha" -> ©nam == ["Kapitola 2"]

def test_single_file_title_equals_album(...):
    # ep.title == work.title, 1 episode -> ©nam absent or == album (match naming pattern 1)

def test_genre_freeform_atom_written(...):
    # program.genre None -> '----:com.apple.iTunes:GENRE' contains b'audiokniha'
    # (regression guard: the gate misread ©gen; freeform is the deliberate location)
```

Use the real `db_session`/`episode_factory` fixtures for the hierarchy; call `tag_audio(path, ep, work, force=True)` then read back with `mutagen.mp4.MP4`.

- [ ] **Step 3: Implement.** In `tag_audio`: replace the `f"{ep.episode_number} of {total}"` with `str(ep.episode_number)`; rework `track_title` assignment to the rule above (delete the `total > 1 and not is_anthology` gate for titles; keep anthology handling ONLY if the read-back of existing anthology folders depends on it — judge from the code and say so in the report). In `_write_mp4`, ensure `"16"` → `trkn=(16, 0)`.

- [ ] **Step 4:** Suite green, lint KEPT. Update `docs/modules/tags/NAMING_CONVENTION.md` if it documents "N of Total" anywhere + `docs/modules/library.md`. Commit `fix: plain track numbers and always-write episode titles in fresh-download tags`

---

### Task 3: Media info capture — populate Asset quality fields + backfill

Asset.bitrate/channels/sample_rate are defined but never populated; Episode.duration_ms often NULL. Quality decisions need them.

**Files:**
- Create: `audiobiblio/library/mediainfo.py`
- Modify: `audiobiblio/acquire/downloader.py` (`_download_audio` fills the fields at COMPLETE)
- Modify: `audiobiblio/cli.py` (new command `backfill-mediainfo`)
- Test: `tests/library/test_mediainfo.py`

**Interfaces:**
- Produces: `audiobiblio.library.mediainfo.read_media_info(path: Path) -> MediaInfo` — frozen dataclass `MediaInfo(duration_ms: int | None, bitrate: int | None, channels: int | None, sample_rate: int | None, codec: str | None, container: str | None)` via mutagen (`File(path).info`); returns all-None on unreadable files (never raises). `apply_media_info(session, asset, path)` writes the fields to the Asset row + `episode.duration_ms` if NULL. CLI `audiobiblio backfill-mediainfo [--limit N] [--dry-run]` sweeps COMPLETE audio assets with NULL bitrate.

- [ ] **Step 1: Failing tests** (use the ffmpeg-generated fixture from Task 2 — extract fixture creation into `tests/fixtures_util.py` helper shared by both):

```python
def test_read_media_info_real_file(silent_m4a):
    info = read_media_info(silent_m4a)
    assert info.duration_ms and info.duration_ms > 0
    assert info.sample_rate and info.channels

def test_unreadable_returns_none_fields(tmp_path):
    bad = tmp_path / "x.m4a"; bad.write_bytes(b"not audio")
    info = read_media_info(bad)
    assert info.duration_ms is None and info.bitrate is None

def test_apply_media_info_fills_asset_and_episode(db_session, episode_factory, silent_m4a):
    ...  # asset.status COMPLETE + episode.duration_ms was NULL -> both populated
```

- [ ] **Step 2: Implement** module + downloader hook (call `apply_media_info` right after `tag_audio` in `_download_audio` — same session) + CLI command (query COMPLETE AUDIO assets with `bitrate IS NULL` and existing file_path, apply, print table, `--dry-run` prints only).

- [ ] **Step 3: Real-data verification:** `uv run audiobiblio backfill-mediainfo --limit 20 --dry-run` then without `--dry-run` against the real dev DB (files exist locally in ~/Downloads/audiobiblio). Quote the output in the report.

- [ ] **Step 4:** Suite green, lint KEPT (library imports core only ✓). Docs: `docs/modules/library.md`. Commit `feat: media-info capture on download + backfill-mediainfo command`

---

### Task 4: Trash module — never delete, always trash, purge after 30 days

**Files:**
- Create: `audiobiblio/library/trash.py`
- Modify: `audiobiblio/core/config.py` (`trash_retention_days: int = 30`)
- Modify: `audiobiblio/acquire/scheduler.py` (daily purge job)
- Test: `tests/library/test_trash.py`

**Interfaces:**
- Produces: `move_to_trash(path: Path, library_dir: Path, reason: str = "") -> Path` — moves file into `{library_dir}/.trash/{YYYY-MM-DD}/{original_name}` (dedupe collisions with `-2`, `-3` suffixes), writes a sidecar `{name}.trashinfo.json` (original absolute path, reason, timestamp) so anything is restorable by hand; returns the trashed path. `purge_trash(library_dir: Path, retention_days: int) -> int` — deletes date-folders older than retention, returns count. Scheduler job `purge_trash` daily.

- [ ] **Step 1: Failing tests** — move creates dated folder + sidecar with original path; collision suffixing; purge removes only folders older than retention (create fake dated dirs, freeze cutoff by passing `now=` param — add `now: datetime | None = None` for testability, no Date.now games); purge returns count; move refuses to trash a path already inside `.trash` (raises ValueError).

- [ ] **Step 2: Implement** (pure stdlib: shutil.move, json). Scheduler: `add_job(_purge_trash_job, IntervalTrigger(hours=24), id="purge_trash", max_instances=1, replace_existing=True)` reading `cfg.trash_retention_days`.

- [ ] **Step 3:** Suite green, lint KEPT. Docs: `docs/modules/library.md` + config template (`config.yaml.template`: add trash_retention_days). Commit `feat: trash module — dated trash with sidecars and daily purge`

---

### Task 5: `upgrade_candidates` table + re-air upgrade detection

The hook: `library/pipelines/ingest.py:_find_existing_episode` already detects re-airs (`url_reair`, lines ~127–134) and today only records an alias. Now it also evaluates.

**Files:**
- Modify: `audiobiblio/core/db/models.py` (`UpgradeStatus` enum + `UpgradeCandidate` model)
- Create: `migrations/versions/<generated>_add_upgrade_candidates.py`
- Create: `audiobiblio/dedupe/upgrades.py` (decision function)
- Modify: `audiobiblio/library/pipelines/ingest.py` (call the evaluator on re-air match)
- Test: `tests/dedupe/test_upgrades.py`

**Interfaces:**
- Produces:
  - `UpgradeStatus` str-Enum: `PENDING_REVIEW`, `STAGED`, `REPLACED`, `KEPT_OLD`, `DISMISSED`.
  - `UpgradeCandidate`: `episode_id FK`, `candidate_url String(1000)`, `candidate_duration_ms Optional[int]`, `owned_duration_ms Optional[int]`, `owned_asset_id FK(assets)`, `status SAEnum(UpgradeStatus) default PENDING_REVIEW`, `staged_path Optional[String(2000)]`, `note Optional[String(500)]`, `created_at`, `resolved_at Optional`. Unique on `(episode_id, candidate_url)`.
  - `audiobiblio.dedupe.upgrades.evaluate_reair(session, episode, candidate_url, candidate_duration_ms) -> UpgradeCandidate | None` — pure decision per spec §4.2:
    - owned AUDIO asset not COMPLETE → return None (normal re-download path already handles it; do not create a candidate)
    - both durations known and `abs(diff) <= 5000` ms → None (same content; alias only)
    - both durations known and `abs(diff) > 5000` → create PENDING_REVIEW candidate (ad-suspect — NEVER auto-resolve, regardless of direction)
    - candidate duration unknown → create PENDING_REVIEW candidate with note "duration unknown"
    - idempotent: existing (episode, url) candidate → return it unchanged.

- [ ] **Step 1: Failing tests** — one test per decision branch above (5 tests), using `db_session`/`episode_factory` + an Asset row set COMPLETE with `episode.duration_ms` set; plus idempotency test.

- [ ] **Step 2: Model + migration** (chain from `20f737dc3b98`; verify heads; upgrade/downgrade cycle; strip drift noise). **Step 3: Implement evaluator.** **Step 4: Wire into ingest** — in the `url_reair` match branch, after `_add_alias`, call `evaluate_reair(...)` passing the discovered entry's `duration_s * 1000` when available (check what the ingest path receives — `upsert_from_item` params; thread duration if it isn't already there). No behavior change for ext_id/url-exact matches.

- [ ] **Step 5:** Suite green, lint KEPT (dedupe imports core only — the evaluator takes ORM objects, creates rows; ingest (library) imports dedupe — ALLOWED? Check the layer contract: library and dedupe are on DIFFERENT tiers (acquire|library above sources|dedupe|tags). library→dedupe is downward ✓). Docs: `docs/modules/dedupe.md`, `docs/workflows.md` §4.2 markers. Commit `feat: upgrade candidates — re-air evaluation with ad-suspect detection`

---

### Task 6: Tag carry-over — old file's curated tags survive replacement

**Files:**
- Create: `audiobiblio/tags/carryover.py`
- Test: `tests/tags/test_carryover.py`

**Interfaces:**
- Produces: `carry_over_tags(old_path: Path, new_path: Path, protect: Sequence[str] = ALL_KNOWN_FIELDS) -> dict` — reads tags from old file (`tags.reader`), reads new file's tags, writes onto the new file every protected field where the OLD file has a non-empty value (old wins — it may carry manual curation; the DB-provenance integration comes in Phase 4, keep this file-level and self-contained); returns the dict of fields written. Never touches the old file. Works for m4a + mp3 (reuse `tags.writer.write_tags`).

- [ ] **Step 1: Failing tests** — using two generated fixtures: old file with title/artist/album/genre set, new file with defaults → after carry-over the new file has old's values; empty old-fields don't blank new ones; returns written-field dict; old file byte-identical after (hash before/after).

- [ ] **Step 2: Implement** on top of `tags.reader`/`tags.writer` public functions (check their exact signatures first; adapt mapping — reader returns its own dict shape).

- [ ] **Step 3:** Suite green, lint KEPT. Docs: `docs/modules/tags.md`. Commit `feat: tag carry-over utility for file replacement`

---

### Task 7: Staging + resolve flow — the upgrade lifecycle API

**Files:**
- Create: `audiobiblio/web/routers/upgrades.py` (+ include in `web/app.py`)
- Modify: `audiobiblio/acquire/downloader.py` (a `download_to_staging(url, staging_dir) -> Path` helper reusing the yt-dlp invocation, no DB writes)
- Test: `tests/web/test_upgrades_api.py`

**Interfaces:**
- Produces REST under `/api/v1/upgrades`:
  - `GET /api/v1/upgrades?status=pending_review` — list candidates (episode title, durations, urls, status)
  - `POST /api/v1/upgrades/{id}/stage` — background task (task_tracker) downloading candidate to `{download_dir}/_staging/upgrade-{id}/`; on success sets status STAGED + staged_path + fills candidate media info (Task 3's `read_media_info`) into `note` (bitrate/duration now measurable)
  - `POST /api/v1/upgrades/{id}/resolve` body `{"decision": "replace" | "keep_old" | "dismiss"}`:
    - `replace`: carry_over_tags(old→staged), move old file to trash (Task 4), move staged file to the old file's library path (same name), update Asset (file_path unchanged, size/bitrate etc. re-read via apply_media_info), status REPLACED + resolved_at
    - `keep_old`: staged file → trash (if staged), status KEPT_OLD
    - `dismiss`: same cleanup, status DISMISSED
    - 409 on illegal transitions (resolve before staged is allowed only for keep_old/dismiss).

- [ ] **Step 1: Failing tests** — TestClient app + upgrades router; stub `download_to_staging` (monkeypatch) and use tmp files for old/staged; assert: replace moves old→trash + staged→library path + carries tags (spot one field) + REPLACED; keep_old trashes staged; dismiss before staging works; illegal transition 409; list endpoint filters by status.

- [ ] **Step 2: Implement** router + staging helper. All file operations go through Task 4's trash / explicit moves — never unlink. **Step 3:** Suite green, lint KEPT, docs (`web.md` endpoints). Commit `feat: upgrade staging and resolve flow (replace/keep/dismiss)`

---

### Task 8: Inbox "Upgrades" section + Console counter

**Files:**
- Modify: `audiobiblio/web/views.py` (inbox route: add upgrades context; index: `upgrade_count` stat)
- Modify: `audiobiblio/web/templates/inbox.html` (Upgrades card above program groups)
- Modify: `audiobiblio/web/templates/index.html` (stat card links to /inbox#upgrades)
- Test: extend `tests/web/test_inbox_view.py`

**Interfaces:** consumes Task 7's endpoints. Upgrades card: one row per PENDING_REVIEW/STAGED candidate — episode title, owned vs candidate duration (mm:ss, diff highlighted, "+2:10 ⚠ possible ads"), source links, buttons: [Stage & compare] (POST stage, then reload), and once STAGED: [Replace] / [Keep old] / [Dismiss] via resolve. Buttons use the existing `apiJson` fetch helper from targets.html — extract it to `audiobiblio/web/static/audiobiblio.js` and include from base.html (both pages use it; remove the inline copy from targets.html).

- [ ] **Step 1:** View context (query candidates with episode joinedload) + template + JS extraction. **Step 2:** Test: grouping function/context includes candidates; duration formatting helper unit-tested (`_fmt_duration_ms(125000) == "2:05"`). **Step 3:** Route census (all 200), suite, lint, docs (`web.md`), workflows §4.2 marker → `[works today: review pairs; auto-upgrade deliberately requires human resolve]`. Commit `feat: inbox upgrades section — stage, compare, resolve`

---

### Task 9: Dedupe page — clusters within the library

Modest scope: surface likely duplicates; merge stays manual + dry-run-first. No automatic deletion.

**Files:**
- Create: `audiobiblio/dedupe/clusters.py`
- Modify: `audiobiblio/web/views.py` (+ `/dedupe` route), `audiobiblio/web/templates/dedupe.html` (new), `base.html` (nav entry after Sources: `Dedupe`)
- Create: `audiobiblio/web/routers/dedupe.py` (merge endpoint) + include in app
- Test: `tests/dedupe/test_clusters.py`

**Interfaces:**
- Produces: `find_duplicate_clusters(session, limit: int = 200) -> list[Cluster]` where `Cluster = {key: str, reason: "same_stripped_url" | "fuzzy_title_same_program", episodes: list[Episode]}`:
  - Tier A: episodes (COMPLETE audio) sharing `norm_url_strip_reair(url)` — exact SQL-side group-by on a computed column is overkill; load candidate URLs and group in Python, capped.
  - Tier B: within one program, normalized titles (existing `_norm_title`) with SequenceMatcher > 0.9 — O(n²) per program, cap per program at 300 episodes, `log()` skip counts.
- `POST /api/v1/dedupe/merge` body `{"canonical_id": int, "duplicate_id": int, "dry_run": true}` — dry-run returns the action list (alias to add, file to trash, episode row to delete); real run: add duplicate's URL as alias on canonical, move duplicate's audio file to trash, delete duplicate's Asset + DownloadJob rows, delete the duplicate Episode. Refuses (409) if duplicate has MANUAL metadata_values rows (protect curation).

- [ ] **Step 1: Failing tests** for `find_duplicate_clusters` (both tiers, generic titles excluded, cap respected) and the merge function's dry-run + real behavior + manual-protection 409 (create a `MetadataValue(origin=MANUAL)` row for the guard test).
- [ ] **Step 2: Implement** clusters + merge (merge logic lives in `dedupe/clusters.py` as `merge_episodes(session, canonical_id, duplicate_id, library_dir, dry_run=True) -> list[str]`; router is a thin wrapper).
- [ ] **Step 3: Page** — clusters table with per-pair [Preview merge] (shows dry-run action list in a `<details>`) and [Merge] (hx-confirm). Nav entry. Route census.
- [ ] **Step 4:** Real-data read-only check: run `find_duplicate_clusters` against the real DB via `uv run python -c ...`, quote counts found (no merging!). Suite, lint, docs (`dedupe.md`, `web.md`). Commit `feat: dedupe page — duplicate clusters with dry-run merge`

---

### Task 10: Phase 3 verification gate

**Files:** none new — checks + report only. Server runs during checks; kill it after; downloads limited to what the steps say.

- [ ] **Step 1:** `uv run pytest -q` + `uv run lint-imports` + `uv run alembic heads` (ONE head) + downgrade/upgrade cycle for this phase's two migrations.
- [ ] **Step 2:** Route census incl. `/dedupe` (all 200).
- [ ] **Step 3: Tags on a real fresh download:** pick ONE small APPROVAL episode in the Inbox, approve (cascade — verify META_JSON/WEBPAGE flip too), run jobs, then read the file: `©nam` non-empty, `trkn=(N, 0)`, freeform GENRE atom present, Asset.bitrate populated. Quote everything.
- [ ] **Step 4: Upgrade path on real data:** re-crawl one target; if any re-air with duration mismatch appears, verify an UpgradeCandidate row + Inbox Upgrades section renders it. If none appears naturally, create a synthetic candidate in the dev DB (INSERT with a real episode's data + fake URL + duration +90s) and walk stage→ (skip actual staging download if the URL is fake — test resolve keep_old path instead) — document which route you took.
- [ ] **Step 5: Trash:** verify `.trash/` layout + sidecar after any Step 4 resolve; `purge_trash` dry logic already unit-tested — do NOT purge real trash.
- [ ] **Step 6:** Backfill sweep: `uv run audiobiblio backfill-mediainfo` (full, real DB) — quote how many assets got media info.
- [ ] **Step 7:** Docs sweep (workflows §4.2 markers vs what was proven; module pages current) — commit any corrections as `docs: phase 3 gate — status markers verified`. Full report.

---

## Deferred (recorded, not this phase)

- Audio-fingerprint/silence-profile ad detection (spec: heuristics may earn auto-resolution later) — dead-end/decision note when attempted.
- DB-provenance-aware carry-over (Phase 4 sync integrates `metadata_values` with file tags).
- Shared-session thread-safety for background tasks (ledger note from P2 final review) — Phase 6 architecture pass.
- UI polish list from P2 ledger (reload-on-error, skipped-tab visual, onclick/onchange) — fold into whichever Phase 3 task touches those templates; otherwise Phase 6.
