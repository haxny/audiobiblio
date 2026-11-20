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
