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
