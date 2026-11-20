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
