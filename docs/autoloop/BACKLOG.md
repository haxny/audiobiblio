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
3. **OPEN — follow_paths "database is locked".** Commit in small batches with
   retry/back-off, never overlap with the sweeps; finish the fingerprint
   backfill (40 k assets) and produce the dry-run report (moved / ambiguous /
   unmatched). Nightly job must not fail silently.
4. **V1 — Independent verifier "does the book have all parts rAPI lists".**
   Daily, report-only first: compare `/serials/{uuid}/episodes` and
   `totalParts` with the book's episodes; add missing parts (budgeted) or
   report. Also detect split books across programs (e.g. works 119015 + 119016,
   Doležal serial). Nothing is called "fixed" until this reports zero on the
   whole DB.
5. **OPEN — Finalize button refuses** instead of silently using the working
   layout when narrator/author is missing; says what is missing.

## P1 — the "follow the files" process

6. **OPEN — metadata.json manifest.** Key `audiobiblio` (guid, work_id,
   serial_uuid, per-file name/size/mtime/fingerprint/tags). Written at finalize
   and for shelved books (write-only first). Then reconcile in dry-run: any
   difference from disk (rename, retag, replaced audio, moved folder found by
   guid) → read real tags, update DB as MANUAL, then update the manifest.
7. **OPEN — "Změny na disku" page**: read-only feed + run button.
8. **OPEN — shelf-queue programme filter + bulk finalize with dry-run.**
9. **OPEN — Variant comparison view**: repeats / ad variants side by side,
   machine picks by duration + ad detection, human only for uncertainty.

## P2 — features other media managers have (survey first)

10. **OPEN — Gap analysis.** Survey Beets, MusicBrainz Picard, Readarr,
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
13. **NEEDS-DECISION (user) — Download intake vs. disk.** audiobiblio itself
    downloads 30-68 GB/day (800-1200 files, mostly rapi-crawl back-catalog:
    Meteor, Osudy, Kontakt, Veda Plus, Mozaika, Musica Antiqua ...) and has
    4,701 audio jobs pending (~240 GB). Real free space (btrfs) ~739 GiB
    (min 383 GiB) -> runway roughly 8-16 days. Options: (a) daily intake cap
    (e.g. 20 GB/day), (b) priority: audiobooks / serials first, talk shows
    (Meteor, Osudy, ...) only when space allows, (c) free space elsewhere
    (nfs2 4.8 TB archive, Drive versions), (d) leave as is, pause by the
    watchdog at < 150 GiB. Loop default until answered: (d).
