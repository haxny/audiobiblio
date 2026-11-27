# Autoloop backlog (ordered; first open item first)

Status: OPEN | DONE | BLOCKED | NEEDS-DECISION

## P0 — correctness and safety

1. **DONE (deployed 70607fc; live effect not yet observed) — Book-scoped identity, with a budget.** Code done on branch `autoloop/book-scoped-identity` (sweeps only, 30 repeat downloads/day, rest APPROVAL); ships after item 2 (disk guard) because df shows < 100 GB free. Original note: Work is on branch
   `autoloop/book-scoped-identity` (find_same_audio / show-sweep match only
   inside the book). Live dry-run: 485 new episodes (219 h, ~18 GB), 474 are
   repeats of recordings held by another book. Needed before shipping:
   repeats are ingested but downloaded under a per-night budget (≤30) and only
   with enough free space; the rest wait as "repeat candidates"; after download
   the existing variant machinery compares them (ad / jingle).
2. **PARTIAL — Disk guard in the downloader (deployed 6d51ed0: floor 5 GB, repeats need 100 GB). STILL OPEN: nightly space report on the health page + cache cleanup trigger.** Refuse to
   start a download when the target volume has < 100 GB free; show free space
   on the health page; run the whitelisted cache cleanup (CHARTER) when low.
3. **DONE (deployed 4470acf; result of the 50 k backfill still to be read, log /app/data/follow_backfill2.log) — follow_paths "database is locked".** Commit in small batches with
   retry/back-off, never overlap with the sweeps; finish the fingerprint
   backfill (40 k assets) and produce the dry-run report (moved / ambiguous /
   unmatched). Nightly job must not fail silently.
4. **PARTIAL (deployed 29abfc6: verifier daily 06:05 + budgeted repair of parts in NO book, <= 30/night, off switch AUDIOBIBLIO_VERIFY_REPAIR=0; acceptance = zero missing_live across the DB, NOT reached: 77 split books remain, see 15) — V1 Independent verifier "does the book have all parts rAPI lists".**
   Daily, report-only first: compare `/serials/{uuid}/episodes` and
   `totalParts` with the book's episodes; add missing parts (budgeted) or
   report. Also detect split books across programs (e.g. works 119015 + 119016,
   Doležal serial). Nothing is called "fixed" until this reports zero on the
   whole DB.
5. **DONE (deployed 25c3270) — Finalize button refuses** instead of silently using the working
   layout when narrator/author is missing; says what is missing.

## P1 — the "follow the files" process

6. **PARTIAL (write path deployed f37a184: record written when a book is shelved; `write-manifests` dry-run default; STILL OPEN: reconcile step; bulk --apply on existing shelves needs the user) — metadata.json manifest.** Key `audiobiblio` (guid, work_id,
   serial_uuid, per-file name/size/mtime/fingerprint/tags). Written at finalize
   and for shelved books (write-only first). Then reconcile in dry-run: any
   difference from disk (rename, retag, replaced audio, moved folder found by
   guid) → read real tags, update DB as MANUAL, then update the manifest.
7. **OPEN — "Změny na disku" page**: read-only feed + run button.
8. **OPEN — shelf-queue programme filter + bulk finalize with dry-run.**
9. **OPEN — Variant comparison view**: repeats / ad variants side by side,
   machine picks by duration + ad detection, human only for uncertainty.

## P2 — features other media managers have (survey first)

10. **DONE (docs/autoloop/GAPS.md; 12 gaps ranked) — Gap analysis.** Survey Beets, MusicBrainz Picard, Readarr,
    Audiobookshelf, tinyMediaManager, Sonarr/Radarr, qBittorrent/AriaNg:
    list features missing in audiobiblio, rank by value for this user; append
    the ranked list to this backlog. Candidates already known: download queue
    UI (progress, pause, retry, priorities, speed limit), rename templates with
    preview + undo, bulk metadata editor, cover management, duplicate finder,
    import wizard / watch folders, notifications, DB backup/restore, stats page.
11. **OPEN — Download Station replacement path** (qBittorrent + VueTorrent or
    AriaNg) — design only, no install without the user.

12. **DONE (finding in LOG) — Where does volume3 space go?** (answer: audiobiblio's own downloads, see item 13) df fell from 119 GB
    (11-18) to 49 GB (11-20) to 20 GB (11-20 evening); btrfs estimate fell by
    the same ~29 GB. Find the writers (read-only first); user media is never
    deleted by the loop.
13. **DECIDED by the independent reviewer agent (user did not answer) -> item 14. Original question — Download intake vs. disk.** audiobiblio itself
    downloads 30-68 GB/day (800-1200 files, mostly rapi-crawl back-catalog:
    Meteor, Osudy, Kontakt, Veda Plus, Mozaika, Musica Antiqua ...) and has
    4,701 audio jobs pending (~240 GB). Real free space (btrfs) ~739 GiB
    (min 383 GiB) -> runway roughly 8-16 days. Options: (a) daily intake cap
    (e.g. 20 GB/day), (b) priority: audiobooks / serials first, talk shows
    (Meteor, Osudy, ...) only when space allows, (c) free space elsewhere
    (nfs2 4.8 TB archive, Drive versions), (d) leave as is, pause by the
    watchdog at < 150 GiB. Loop default until answered: (d).
14. **PARTIAL (soft tier flag done on branch autoloop/soft-tier 8beb5a9, not deployed; still open: rapi-crawl adding no new priority<0 jobs while the flag is set, the 14-day expiry tier, per-tier dry-run numbers) — Intake tiers (reviewer decision for 13: option d + b, soft tier).**
    No hard daily cap. Watchdog stays the only hard stop (pause < 150 GiB,
    resume > 250 GiB, unallocated < 50 GiB pauses everything). Reorder PENDING
    jobs only: tier 1 = playableTill within 14 days; tier 2 = audiobooks,
    readings, serials; tier 3 = talk / magazine back-catalog (Meteor, Osudy,
    Kontakt, Veda Plus, Mozaika, Portréty ...). Soft tier: while real free is
    150-300 GiB only tiers 1-2 download (tier 3 waits, resumes > 350 GiB);
    rapi-crawl adds no new tier-3 jobs while free < 300 GiB. Nothing is
    cancelled or deleted. Ship after a dry-run on a DB copy printing jobs + GB
    per tier. Each daily space-watch entry in LOG.md: GB downloaded, jobs left
    per tier, unallocated GiB, pause/tier state. Ask the user to confirm the
    300/350 and 150/250 GiB thresholds.
15. **OPEN (needs the user's approval before any merge) — Split books (~30 % of the checked multi-part books!).** UPDATE 2026-11-20 22:40: of 256 books checked, 77 are split (377 parts held by another work), 8 have 32 parts in no book (now repaired nightly), 66 only have expired parts. Earlier text: The first live verifier run (200 books) found 12 incomplete
    books, most split over two works that share one rAPI serial: Nabokov
    Lolita (118748 holds parts 1, 11-22; 2-10 elsewhere), Chramostova
    (120395 / 122751), Viewegh (124397 / 124392), Machacek (124232 / 124248),
    Strasidelnicka (2889 / 122750), plus Dolezal (119015 / 119016). Same serial
    UUID = certainly the same book. Plan: list each pair in a dry-run merge
    plan (merge_works already exists: dry-run + MANUAL-metadata protection),
    ask the user, never bulk-merge unattended (charter hard limit). Prevention:
    stamp serial_uuid on discovered books so every ingest path finds them.
16. **DONE (deployed 13eaad4; FIRST real run tonight 04:50 — not yet observed) — Scheduled DB backup with retention + restore check** (GAPS #1).
17. **PARTIAL (API deployed: GET/POST /api/v1/system/intake = both pause switches + pending per tier; card with buttons on /system deployed; STILL OPEN: real btrfs free space in the UI — the container cannot read it, needs a host-side writer) — Space + intake control in the UI** (GAPS #2): real free space, pause
    / soft-tier state, queue per tier, one-click pause.
18. **PARTIAL (deployed: acquire/forecast.py, shown in the intake API + card: GB/day, avg file, queue GB) — STILL OPEN: reconcile the forecast with real btrfs usage (DB says 110 GB/day, real free dropped ~1 GB/h), per-programme breakdown.** Stats + disk forecast (GAPS #7).
19. **OPEN — Per-book history timeline** (GAPS #3).
20. **OPEN — Event notifications** (GAPS #5).
21. **OPEN — Perceptual audio fingerprint for ad/jingle variants** (GAPS #4).

- [ ] 22 Ad/jingle variants at the source (user 2026-11-27, Prosecke peklo): rozhlas changes streams over time (parts 1,4,5,6 had +30.5 s on 9-17 Nov; on 27 Nov only 1 and 6 do). Per file store source URL, manifest duration, real duration, download time. When the same URL yields a different length (anything 5-120 s, not just 20-40), KEEP BOTH versions, align audio, and learn from the pairs (jingle vs ad vs something else) before deciding which is canonical.
- [ ] 23 REVIEW-mode targets never queue jobs: Rozhlasove listovani (ostrava, target 678) was REVIEW/NEWS-TALK, so Sikmy kostel part 3 (episodes 437935, 448873) was indexed with no download job. Flipped to AUTO/BOOK on 2026-11-27 and queued by hand. Audit all targets whose indexed works look like audiobooks/serials but sit in REVIEW; alert on episodes older than 24 h with no job.
