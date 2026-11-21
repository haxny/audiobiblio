# Autoloop log (newest at the bottom)

## 2026-11-20 — setup
- Charter, backlog and this log created. Loop not yet run.
- State at start: `main` = 65220e3 (+ docs). Uncommitted work moved to branch
  `autoloop/book-scoped-identity`: `find_same_audio` and show-sweep match only
  inside the target book; 3 new tests (RED without the fix, GREEN with it);
  full suite 1307 passed, layer contract kept.
- Live dry-run (copy of live DB, real rAPI reads): 485 new episodes, 474 are
  repeats held by another book (~18 GB). Not deployed. Dolezal part 2 was
  created (work 119015). -> backlog item 1.
- Not yet verified: crawler (ingest.py) behaviour on live data with the scoped
  match; follow_paths fingerprint backfill (crashed on "database is locked").
- Open from earlier: recycle bin of share `public` is emptied but still enabled
  (needs DSM UI); Synology Drive is re-uploading old photos (user decision).

## 2026-11-20 — iteration 1: backlog item 1 (book-scoped identity + budget)
- **Outcome: PARTIAL — implemented and verified, deliberately NOT deployed.**
- Done (branch `autoloop/book-scoped-identity`): book-scoped same-audio matching
  is opt-in (`same_book_only`), used only by show-sweep / serial-sweep; HTML
  crawler unchanged (its effect on live data is not measured). Repeats of a
  recording owned by another book are ingested; downloads run under
  `REPEAT_DOWNLOADS_PER_DAY = 30`, the rest become `APPROVAL` jobs.
- Verified: 1310 tests pass, layer contract kept; 3 tests were RED without the
  fix; budget + "crawler stays global" tests added.
- Live dry-run (clean copy of the live DB, real rAPI reads): 490 new episodes,
  474 repeats; jobs: 30 repeat PENDING, 444 repeat APPROVAL, 16 other PENDING.
  Dolezal part 2 was created (episode in work 119015 — the book is still split
  across works 119015/119016, see backlog V1).
- Why not deployed: my own rule — no download-queuing change while `df` shows
  < 100 GB free on the target volume (volume3: 49 GB by df; btrfs estimates far
  more). Next: item 2 (disk guard), then ship both.
- Not verified: crawler path; 444 parked rows in the approval queue (UI noise);
  rAPI show-sweep errors for 3 dead show URLs (404) are pre-existing.
- Housekeeping: deleted two stale DB copies made by the loop in the scratchpad
  (~1.9 GB); Mac free space is 38 GiB (96 %).

## 2026-11-20 — iteration 2: backlog item 2 (downloader disk guard) + ship items 1-2
- **Outcome: DONE with one defect found and fixed after deploy.**
- Implemented: free-space guard in `run_pending_jobs` (held job stays PENDING;
  unreadable disk never blocks; thresholds via env). Tests: 5 (RED without it),
  full suite 1315 passed, layer contract kept.
- Deployed to NAS: 70607fc (items 1 + 2), then 6d51ed0. Health 200
  (`/api/v1/health`), 15 jobs registered, no errors in the first minutes.
- **Defect (mine):** first version used a 20 GB hard floor by `df`. After deploy
  `df` inside the container showed 20 GB while btrfs reports ~700 GiB
  unallocated -> that floor could have stopped ALL downloads. Lowered to 5 GB
  within minutes (6d51ed0); repeats still need 100 GB by df (so they wait).
- Not yet observed: first nightly show-sweep with the new matching (05:41) —
  expect Dolezal part 2 in work 119015 and ~444 parked repeats (APPROVAL).
- New finding (backlog 12): volume3 free space fell ~29 GB in two days; cause
  unknown, read-only investigation running.
- Not done: nightly space report / health page, in-app cache cleanup trigger.

## 2026-11-20 — iteration 3: backlog item 12 (where does volume3 space go?)
- **Outcome: DONE (diagnosis) + one more defect in my own guard, fixed and deployed.**
- Finding: audiobiblio itself. Completed AUDIO download jobs per day: 11-10..11-20
  = 640-1200 files, 30-68 GB/day (11-18: 68 GB). Since 11-18, 2,496 of 2,520
  came from `rapi-crawl` (back-catalog of Meteor, Osudy, Kontakt, Veda Plus,
  Mozaika, Musica Antiqua ...), 127 GB. 4,701 AUDIO jobs are still PENDING
  (~240 GB at ~51 MB average). Not caused by items 1-2 (repeats are parked).
- Correction of an earlier reading: `assets.updated_at` is touched by my
  fingerprint backfill (35 k rows on 11-18), so per-asset dates are unusable;
  completed jobs (`finished_at`) are the reliable measure.
- **Defect (mine, caught before it hurt):** df on this btrfs shows only free
  space inside allocated chunks (~20 GB, falling ~3 GB/h) while btrfs reports
  ~739 GiB free (712 GiB unallocated). Even the 5 GB df floor would have stopped
  ALL downloads around 01:30 tonight. Replaced by a `PAUSE_DOWNLOADS` flag file
  (deployed 6d66f94, 1316 tests pass). The loop measures real free space with
  `btrfs filesystem usage` each iteration (charter: pause < 150 GiB, resume
  > 250 GiB). Repeats still need 100 GB by df, i.e. they wait.
- Pause state now: NOT paused (739 GiB free).
- Not verified: that writes keep succeeding when df reaches ~0 (btrfs should
  allocate new chunks; unverified). The watchdog is the safety net.
- Decision needed from the user: backlog item 13 (intake policy). Default
  until answered: leave intake as is, watchdog pauses at < 150 GiB.

## 2026-11-20 — iteration 4: backlog item 3 (follow_paths "database is locked") + decision on item 13
- **Outcome: DONE (deployed 4470acf, 1324 tests pass, layer contract kept).**
- Cause confirmed: the DB timeout is already 60 s, but another writer held the
  lock longer; one unlucky batch UPDATE killed the whole nightly job and all
  computed fingerprints were lost.
- Fix: fingerprints are written in batches of 100 with retry (2, 4, 8, 16 s);
  on a persistent lock the backfill stops cleanly (never raises) and the next
  run continues; writes keep `updated_at` untouched (bookkeeping is not an
  edit); the nightly job retries `follow_paths` up to 3x (120 s apart).
  8 new tests incl. transient lock, persistent lock + resume, no updated_at bump.
- Started in the container: `audiobiblio follow-paths --backfill 50000`
  (dry-run follow, log `/app/data/follow_backfill2.log`). Result not yet read.
  NOTE: the nightly job at 02:40 runs follow_paths with apply=True (deployed
  earlier at the user's request); it only re-points DB paths, never deletes.
- Space watch 21:16: btrfs free (estimated) 738.6 GiB, unallocated 711.98 GiB,
  df 18 GB (misleading), not paused. Intake still ~45 GB/day.
- **Item 13 decided by the independent reviewer agent** (user silent for one
  cycle, per charter): option d + b with a soft tier, no hard cap -> backlog 14.
  Added charter rule: unallocated < 50 GiB pauses all downloads. The reviewer's
  warning for the user: the queue alone (~240 GB) fits the 738 GiB estimate but
  not the 382 GiB minimum; in that worst case the talk back-catalog waits.
  Option (c) (free space on other volumes) is the user's call only.

## 2026-11-20 — iteration 5: backlog item 4 (V1 verifier)
- **Outcome: PARTIAL — verifier built, deployed (d7f3d19), first live report not yet read.**
- Built `acquire/serial_verify.py` (report-only): for each serial book compares
  the parts rAPI lists (`/serials/{uuid}/episodes`) with what the book holds
  (by episode id, alias id or part number) and splits the gaps into
  missing-live (downloadable), missing-expired, and held-elsewhere. Unstamped
  books are matched through an episode that carries an rAPI UUID (reported as
  `discovered`, nothing is stamped). Network trouble becomes a finding.
- Delivered: CLI `audiobiblio verify-serials [--limit N] [--json F] [--fix]`
  (`--fix` = manual, adds missing live parts through the budgeted path), daily
  job 06:05 writing `serial_verify.json` next to the DB. Tests: 11 new (TDD,
  RED first), full suite 1335 passed, layer contract kept.
- Running now on the live DB (report only): `verify-serials --limit 200`
  -> `/app/data/serial_verify_manual.json` / `.log`.
- Limits: 200 books per run (stamped first, newest first), so full coverage of
  the DB takes several days; no paging beyond 100 parts per serial (flagged as
  `truncated`). The "fixed" claim for missing parts stays unmade until the
  reports reach zero on the whole DB.
- Side effect to note: the container restart for this deploy killed the
  fingerprint backfill at 42,100 of 43,348 (97 %); restarted it
  (`follow_backfill3.log`). The earlier dry-run report of moved / ambiguous /
  unmatched files was lost with it and is still pending.
- Space watch 21:47: btrfs free 737.5 GiB, unallocated 711.98 GiB, not paused.

## 2026-11-20 — iteration 6: first live verifier report -> noise fix, split books named
- **Outcome: DONE (deployed 85988d9, 1339 tests pass) — and the report proves the
  split-book problem is NOT solved.**
- First live run (200 books): 12 incomplete books, 41 missing-live parts, but
  183 of 200 results were HTTP 404 noise (single news episodes whose id is not
  an rAPI episode). Fixed: 404 during discovery = not a serial book (skipped);
  a 404 for a STAMPED serial stays a finding; selection now prefers stamped
  books and books with >= 2 rAPI-keyed parts or a known total.
- The report now lists `held_in` (the works that hold the missing parts).
  Reading the first run: 6 of the 12 are one book split over two works
  (Lolita, Chramostova, Viewegh, Machacek, Strasidelnicka; Dolezal known from
  before). That is the Hrabal pattern again, still present after the
  2026-11-17 fix -> backlog item 15 (needs the user's approval to merge).
- Second live run (up to 300 books) started: `serial_verify_manual2.json`.
- Fingerprint backfill FINISHED: follow-paths dry run `moved=0 ambiguous=0
  unmatched=0` -> no fingerprinted asset has a dead path today (the earlier
  pending report is closed).
- Space watch 22:13: btrfs free 736.4 GiB, unallocated 711.98 GiB, not paused.

## 2026-11-20 — iteration 7: second verifier run -> budgeted repair of parts in no book
- **Outcome: DONE (deployed 29abfc6, 1345 tests pass, layer contract kept) — plus a sobering measurement.**
- Second live run (256 books checked, 593 skipped as non-serial): 151 incomplete.
  * **77 books are split**: 377 missing parts are held by another work (e.g.
    Nabokov Lolita 118748/118749, Gulliver 118746/118747, the 8 one-part works
    114326-114333 "Poslední seance", Katedrála, Hajajafón ...). Older data from
    before the serial grouping; the 2026-11-17 fix only prevents NEW splits.
    -> backlog 15 (merge plan needs the user).
  * **8 books / 32 parts are in NO book** though rAPI lists them as live (e.g.
    work 116287 '50 let od přistání na Měsíci': rapi-crawl took parts 1-2 of 7
    on 2026-10-10, parts 3-7 never arrived). Root cause of the partial ingest
    NOT established (not the show-sweep 100-item window: the parts date from
    2019). The verifier-driven repair is cause-independent.
  * 66 books only miss EXPIRED parts: nothing downloadable.
- Built `repair_missing` (acquire/serial_verify.py): adds parts held by no book,
  <= 30 per night, never repeats of parts held by another work, skips books whose
  serial is stamped on another work. `_expand_serial(only_parts=...)`. Nightly
  after the verifier (off: AUDIOBIBLIO_VERIFY_REPAIR=0); CLI
  `verify-serials --fix --max-parts N`. 10 new tests, TDD.
- Started once by hand on live: `verify-serials --limit 300 --fix --max-parts 30`
  (`serial_verify_manual3.json/.log`). Result not yet read.
- Still wrong / not done: the scheduler job name still says "(report only)";
  the merge of the 77 split books; the cause of partial rapi-crawl ingests.
- Space watch 22:40: btrfs free 735.4 GiB, unallocated 711.98 GiB, not paused.

## 2026-11-20 — iteration 8: INCIDENT (Container Manager down) + backlog item 5
- **Incident, 23:17 NAS time: the Docker daemon / Container Manager package went down.**
  `synopkg status ContainerManager` = stop; `dockerd` was a defunct process; the
  app answered nothing (`health 000`). Later `/var/run/docker.sock` and
  `docker.pid` were re-created at 23:17, i.e. DSM is restarting the package (its
  last start on 11-02 took ~13 min). I did NOT restart or touch it (other
  containers share it; charter: no stopping/starting other services).
- Evidence gathered (read-only): no OOM kill in dmesg; load average 21 (IO 17-18,
  kswapd busy, only ~830 MB free + 22 GB cache); 87 days uptime. Cause NOT
  established. My own concurrent load at that time (the manual `verify-serials
  --limit 300 --fix`, ~900 rAPI requests, and the fingerprint backfill) is a
  possible contributor — UNVERIFIED. If it repeats, the verifier and the
  backfill get a lower priority / smaller limits.
- Unknown until the daemon is back: whether the manual repair run
  (`serial_verify_manual3.*`) finished or was killed with the container, and
  whether the app container comes back by itself. No data was touched by me.
- Item 5 done locally (3b514ce, branch `autoloop/finalize-refuse`, 1348 tests
  pass, layer contract kept): the finalize button now answers 409 in preview AND
  apply when a curated-program book lacks author and/or narrator, naming what
  is missing; unmapped programs keep the working layout (by design). Also fixed
  the scheduler job name ("report only" was wrong since 29abfc6). TO DEPLOY once
  Docker is back.
- Space watch 23:17 (before the incident): btrfs free 739.3 GiB, unallocated
  711.98 GiB.

## 2026-11-20 — iteration 9: incident follow-up (no code change, nothing deployed)
- 23:38 NAS: the app answers again (`/api/v1/health` 200) but the Docker CLI still
  returns nothing (`docker ps` / `docker info` empty within 20-30 s) and
  `synopkg status ContainerManager` still says stop; `docker.sock` was
  re-created at 23:17 and again at 23:27 (DSM keeps restarting the daemon).
  Load average ~21, IO pressure 16-18. Nothing deployed while the daemon is not
  answering (a deploy needs `docker compose build/up`).
- **Evidence the instability is old and not caused by the loop:**
  `/var/run/docker/` holds 30 `goroutine-stacks-*.log` dumps (the daemon was
  hung and restarted), from 2026-08-29 to 2026-11-19, roughly one every three
  days; the latest before tonight was 2026-11-19 18:22. My concurrent load
  (verifier + backfill) may have made tonight worse: still UNVERIFIED.
- Space watch 23:38: btrfs free 746.8 GiB (up from 739 GiB: downloads were
  interrupted), unallocated 711.98 GiB, not paused.
- Waiting (no action): the daemon to answer; then deploy 3b514ce (item 5), read
  `serial_verify_manual3.*` and re-measure. Not touching Container Manager.

## 2026-11-21 — iteration 10: repair result read from the host; soft tier built; Docker still silent
- **Manual repair run finished at 23:09 CET (before the 23:17 incident):**
  `verify-serials --limit 300 --fix --max-parts 30` -> 256 books checked, 151
  incomplete, **30 parts added in 8 books**, 0 split books skipped (read from the
  host volume `/volume2/@docker/volumes/audiobiblio_data/_data`, no docker
  needed). Its load ended 8 min before the daemon incident, which weakens the
  "my load caused it" suspicion (still unproven either way).
- The 30 repair jobs are PENDING at episode priority 10 (the top tier: only 172
  pending jobs have priority 10, 4,591 have -10); audio downloads run at
  24-67 files/hour, the app kept downloading through the Docker outage (last
  audio job 23:52). Expect them done within hours. Work 116287 now lists parts
  1-7 (was 1-2).
- Soft tier built (branch `autoloop/soft-tier`, 8beb5a9, 1347 tests pass, layer
  contract kept): `PAUSE_LOW_PRIORITY` flag holds priority < 0 jobs; charter got
  the rule (< 300 GiB set, > 350 GiB clear). NOT deployed.
- **Docker CLI still returns nothing** (00:04): `docker.sock` was re-created at
  23:17, 23:27 and 23:52; package status `stop`. Deploys blocked. Waiting:
  `autoloop/finalize-refuse` (3b514ce) and `autoloop/soft-tier` (8beb5a9).
- Space watch 00:01: btrfs free 793.5 GiB (+47 GiB since 23:38, cause unknown —
  not a loop action), unallocated 711.98 GiB, df 73 G. Not paused, no flags set.

## 2026-11-21 — iteration 11: Docker back; items 5 + soft tier deployed; item 6 (manifest) write path
- **Docker/Container Manager is back (00:36):** containers up (audiobiblio
  restarted by Docker ~23:58), load 7.8 (was 21), health 200. Deployed the two
  waiting branches as 25c3270: item 5 (finalize refuses incomplete books) and
  the soft-tier flag. 1350 tests pass, layer contract kept.
- Repair follow-up (read from the host DB): the 30 repair jobs are still PENDING at
  priority 10 behind older priority-10 jobs (172 in that tier, ~50 downloads/h);
  no job stuck in RUNNING; PENDING audio 4,713 (was 4,764).
- **Item 6 (manifest), write path** — deployed f37a184, 1357 tests pass:
  `library/manifest.py` writes key `audiobiblio` into the folder's metadata.json:
  stable book guid (provenance store, no migration), serial UUID, per audio file
  name / size / mtime / payload fingerprint / last seen tags. ABS's own keys are
  preserved; rewritten only when content changes (timestamp excluded); shared
  folders (collections) are skipped. Hooked into `publish_to_abs`, i.e. NEW
  shelvings get it. `audiobiblio write-manifests` is a dry-run by default.
- **NOT done / needs the user:** `write-manifests --apply` on the existing shelves
  would add a key to many metadata.json files in the user's library (a bulk
  change to user folders; the charter forbids bulk changes on user data without
  approval). Dry-run started on live (`write_manifests_dry.log`), result unread.
  The reconcile step (compare record vs disk, read real tags, update DB) is
  not built.
- Space watch 00:36: btrfs free 793.6 GiB, unallocated 711.98 GiB, df 74 G; no
  flags set.

## 2026-11-21 — iteration 12: item 10 (gap analysis); manifest dry run read; repair queue position
- **Item 10 DONE (docs only):** `docs/autoloop/GAPS.md` — 12 gaps ranked for this
  user, new backlog items 16-21. The comparison tools are described from memory,
  not re-verified against their current docs (stated in the file); what
  audiobiblio already has was checked in the repo. Top finding: **no scheduled DB
  backup job in `scheduler.py`** (1 GB SQLite; Hyper Backup coverage of the
  Docker volume unverified) -> item 16, next.
- Manifest dry run on live: `write-manifests` would write **296 of 1,058**
  shelved books (the rest are folders shared by several works, skipped by
  design). Nothing written. `--apply` still needs the user.
- Repair downloads: priority-10 pending fell 172 -> 84, the 30 repair jobs sit at
  positions 54-83 (~50 downloads/h) -> done in about an hour; no errors in the
  last 4 h; PENDING audio 4,675 (was 4,713).
- Space watch 01:19: btrfs free 817.5 GiB (rising, cause unknown, not a loop
  action), unallocated 711.98 GiB, df 97 G; no flags; app healthy.
- Deploy freeze 02:00-06:00 starts now; item 16 is built next and shipped after 06:00
  unless it is a no-op for the nightly jobs.

## 2026-11-21 — iteration 13: backlog item 16 (nightly DB backup)
- **Outcome: DONE (deployed 13eaad4 at 01:55, before the 02:00 freeze); first real run still to be observed.**
- `core/db_backup.py` + job `db_backup` at 04:50: sqlite online-backup (consistent
  with a live WAL) -> `PRAGMA quick_check` on the copy -> gzip -> prune only after a
  new good copy exists (keep 3, only our own `db-*.sqlite3.gz` names) -> archive
  re-read to check the gzip CRC. Refuses when free space < 2x the DB. Stored in
  `/app/data/audiobiblio/backups/` on the same volume (volume2, 627 GB free).
- Measured on a copy of the live DB (998 MB): 13 s, 301 MB archive, verify 1 s ->
  3 copies ~0.9 GB. 8 new tests (RED first), full suite 1365 passed, layer
  contract kept.
- Honest limit: same volume as the DB, so it protects against logical damage
  (bad bulk edit, corrupt page), NOT against losing the volume; whether
  Hyper Backup covers the Docker volume is still unverified (user).
- Housekeeping note: `/app/data/audiobiblio/db.backup-pre-serial-uuid.sqlite3`
  (930 MB, 2026-11-17) is outside the loop's deletion whitelist; it is now
  superseded by the nightly backups — a candidate for the user to delete.
- Space watch 01:51: btrfs free 818.2 GiB, unallocated 711.98 GiB; volume2 free
  627 GB; no flags; app healthy.
- Deploy freeze 02:00-06:00 now. Next: read the 02:40 follow_paths run, the 04:50
  backup and the 06:05 verifier after they ran; build item 17 offline meanwhile.

## Iteration 14 — 2026-11-21 02:52 CET (watch only)
- Space: btrfs free 817 GiB, unallocated 712 GiB, no pause flags, health 200.
- follow_paths (02:40) not in the log yet: container clock is UTC (01:52 UTC now), so the job runs at 03:40 CET. DB backup (04:50) likewise not yet run; `backups/` does not exist yet. Nothing deployed (freeze 02:00–06:00 CET).
- Next: wake ~04:00 CET to read follow_paths; then after the backup and the 06:05 verifier run (times may be UTC-shifted by 1 h).

## Iteration 15 — 2026-11-21 03:48 CET (watch only)
- follow_paths ran 02:40 UTC (03:40 CET): fingerprinted 351 new files, moved=0 ambiguous=0 unmatched=0, no lock errors, ~97 s. First clean nightly run since the retry/batch fix.
- Cron jobs run on UTC: backup 04:50 UTC = 05:50 CET, verifier 06:05 UTC = 07:05 CET, sweeps likewise. Space: btrfs free 816 GiB, unallocated 712 GiB, no flags, health 200. Nothing deployed.
