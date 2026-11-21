# Gap analysis: what other media managers have that audiobiblio lacks

Written 2026-11-21 by the autoloop (backlog item 10). The comparison tools
(Beets, MusicBrainz Picard, Readarr/Sonarr, Audiobookshelf, tinyMediaManager,
qBittorrent/AriaNg/Download Station) are described **from my own knowledge, not
re-checked against their current documentation**; what audiobiblio already has
was checked in this repo (routers, navigation, CLI, scheduler).

## What audiobiblio already has (do not rebuild)
Overview, worklist (inbox), download queue (`/jobs`), library, programs,
shelf queue, gaps page, import findings, duplicate works / files / copies of
shelved books, sources (crawl targets + approval modes), catalog rebuild,
segmentation, live recordings, logs, system page + stats; JDownloader 2 send-to;
health detectors with a daily digest (notifier); provenance (MANUAL > ENRICHED >
FILE > SCRAPED); path history + payload fingerprints; ABS metadata.json publishing.

## Ranked gaps (value for THIS user = a Czech radio-audiobook archive on a NAS,
## edited by hand in foobar / cmdr, short on disk)

| # | Gap (seen in) | Why it matters here | Effort | Note |
|---|---|---|---|---|
| 1 | **Scheduled DB backup with retention + restore check** (Readarr/Sonarr "Backups") | The 1 GB SQLite DB holds all provenance and manual edits; only hand-made copies exist (`db.backup-*.sqlite3`); NAS daemon crashes every ~3 days. No backup job found in `scheduler.py` (verify whether Hyper Backup covers the Docker volume). | S | Highest risk/value ratio |
| 2 | **Space + intake control in the UI** (Download Station / qBittorrent: pause, priorities, schedule) | Pause flags are files set by the loop; the user cannot see or flip them. Show real free space, the pause/soft-tier state, queue per priority tier, one-click pause. | S-M | Needs a host-side space reading (the container only sees misleading `df`) |
| 3 | **Per-book history timeline** (Sonarr/Readarr "History") | "What happened to this book?" — ingest, downloads, renames/moves (`path_history` exists), edits (provenance), merges. Answers the user's repeated question without reading logs. | M | Data mostly exists |
| 4 | **Perceptual audio fingerprint for variants** (Beets chromaprint/AcoustID) | The payload fingerprint is exact; ad/jingle variants of one broadcast differ by seconds, so repeats cannot be matched automatically. Needed for "download repeats, compare after". | M-L | `fpcalc` in the image; compare per part |
| 5 | **Event notifications** (Sonarr/Readarr "Connect") | Digest exists for health; add: book complete, parts added by the repair, downloads paused/resumed, merge candidates found. | S-M | Reuse `health_notifier` |
| 6 | **Rename templates with preview + undo** (Picard/tMM/Readarr) | Finalize uses one fixed pattern; the user renames by hand. A preview of the pattern with an undo from `path_history`. | M | Matches "I prefer cmdr" only partly |
| 7 | **Statistics + disk forecast** (Readarr/ABS stats) | GB/day by program, runway to the 150 GiB floor — the question that came up this week. | S-M | `/stats` exists but is counts only |
| 8 | **Blocklist / never-download rules** (Sonarr "Blocklist") | Ignore a program, series or episode permanently without disabling the source. | S | Targets have approval modes; episode-level missing |
| 9 | **Chapter editor + chapter import from files** (ABS) | Long radio episodes; ABS handles chapters; we only have segmentation. | M | Lower priority |
| 10 | **Metadata match from external providers with side-by-side diff** (ABS/Picard) | Enrichment proposals exist (databazeknih, mluvenypanacek); a uniform diff/accept UI across providers. | M | Partly there |
| 11 | **Bulk metadata editor in the web** (tMM/Picard) | User prefers cmdr; low value. | M | Skip |
| 12 | **OPDS / API tokens** (ABS/Calibre) | Not requested. | M | Skip |

## Download Station replacement (backlog 11)
audiobiblio already talks to **JDownloader 2** (compose service + router). If the
need is torrents: qBittorrent (+ VueTorrent) is the usual choice; for plain
links AriaNg over aria2. No install without the user.

## Proposed order for the loop
1 (backup) -> 2 (space/intake UI) -> 7 (stats + forecast) -> 3 (history) -> 5
(notifications) -> 4 (perceptual fingerprint) -> 8 -> 6 -> 10 -> 9.
