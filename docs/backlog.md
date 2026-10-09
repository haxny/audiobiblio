# Backlog

Agreed work that is deferred (not urgent). Newest first within each section.
Each item: what, why, and the decided approach. GUI: http://nasx:8321

## Data integrity

- [ ] **Downloads overwrote each other — 392 episodes in 18 programs (found 2026-10-09).**
  `build_paths_for_episode` produces the same stem for episodes with the same
  title (dropped as an echo of the album) and the same number (all `1`); the
  downloader overwrote silently. Worst: Oldies 233 (238 downloads → 5 files),
  Desky, pásky, vzpomínky 47, Folklorní notování 46, Všudykuk 39.
  - [ ] Collision-proof stems for non-book programs: number + air date `YYYYMMDD`
        (`Oldies aneb Historie z cernych drazek - 287 - 20250824.m4a`).
  - [ ] Downloader guard: never overwrite a file owned by another episode.
  - [ ] Recovery: mark overwritten assets MISSING, re-queue; still-live ones
        download, expired → GONE. Map the surviving files to the right
        episodes (download times, `info.json`).
- [ ] **Merge older duplicates in the DB**: the same broadcast as a GONE archive
      stub + a downloaded copy in the catch-all program "mujrozhlas" (e.g.
      Velká pohádka: Perníková pohádka, Houbové čarování). New code no longer
      creates them; existing ones are not merged.

## Music programs (not audiobooks)

- [ ] **Oldies aneb Historie z černých drážek** — http://nasx:8321/works/9620
      (source https://ostrava.rozhlas.cz/oldies-jako-na-dlani-8289348) is a pure
      music program: download and organize, but never into audiobooks.
  - [ ] Target `/volume3/music/mujrozhlas.cz/Oldies aneb Historie z cernych drazek (CRo2)/`
        — indexed by Plex/Plexamp, not Audiobookshelf.
  - [ ] Episode number = order by air date (rAPI has no part numbers), full
        air date `YYYYMMDD` in the name.
  - [ ] Plex tags: album artist `Jiri Tieftrunk`, album = program, track = number,
        title = `YYYYMMDD` + topic, genre `Oldies; CRo2`.
  - [ ] Generic "music" layout for all music programs (not just Oldies).
- [ ] **Song identification → split into tracks.** rozhlas publishes no tracklists
      for Oldies. Option A (free) first: speech/music segmentation
      (e.g. inaSpeechSegmenter) → Chromaprint/AcoustID → MusicBrainz; trial on 3
      episodes, measure the hit rate. Option B (paid) AudD / ACRCloud only after A.
      Shazam: no official API for this — no.

## Library & shelving

- [ ] **Nightly library inventory** — register every book folder on the curated
      shelves (fiction, nonfiction, 4kids) incl. the user's hand copies from the
      Mac: path, files, measured total length, parsed author/title/narrator/year.
      Before shelving/downloading, check "same work + same length ± tolerance"
      → no second copy (Chvilka štěstí case). Feeds mluvenypanacek matching.
- [ ] **Split work 11244** "Pokračování za pět minut" — http://nasx:8321/works/11244 —
      four different books in one work (Le Roy: Trhan Kuba; Lovecraft: Barva z
      vesmíru; Vilímek: Z Prahy k Baltickému moři v balonu; Maupassant: Kulička),
      then re-shelve each (`scripts/reshelve_work.py`).
- [ ] **Čtenářský deník books ready to shelve** — metadata review like Staré
      pověsti české (narrator, first-edition year, recording year):
      Bylo nás pět http://nasx:8321/works/24307 · Marketa Lazarová
      http://nasx:8321/works/24308 · Švejk http://nasx:8321/works/24314 ·
      Babička http://nasx:8321/works/24285 · Okresní město http://nasx:8321/works/24290
- [ ] **Near-duplicate decisions (user)** — http://nasx:8321/chaos-dups:
      Chvilka štěstí (4 copies), Krvavá pavlač (2 copies).
- [ ] **Work page shows file location** (share path, e.g.
      `eBOOKs/audiobooks/audiobooks/<Program>/`) — user could not find where files are.
- [ ] **Release AUTHOR-CHECK / WAITING-METADATA queues** (~490 books) — bulk
      enrichment of author + narrator (original plan item 2).
- [ ] **Mac → NAS transfer**: 242 files (3.7 GB) from `~/Downloads/audiobiblio`
      not on the NAS — cd.cz music 40 albums (→ `/volume3/music/cd.cz/`), SFT 48,
      mujrozhlas 30, Povídky klasiků 9, Mezi kopci 5, Pardubice 1. Staging
      `eBOOKs/audiobooks/_mac_import/` → import scan; delete on the Mac only after
      verification.

## Catalog sources

- [ ] **mluvenypanacek.cz** (WordPress REST, 130 417 records; raw dump in
      `/app/data/audiobiblio/panacek/posts.jsonl`, `scripts/panacek_dump.py`).
      Principle (user): metadata + recorded length vs our measured length →
      tell versions apart, detect duplicates and (in)completeness.
  - [ ] Parse records: credits, cast with roles, recording dates per part,
        premiere/reprises, CD release with length, numbered parts.
  - [ ] Match against our works and the library inventory (by length).
  - [ ] Gap report: aired but missing (readings, plays, short stories first).
  - [ ] Incremental sync (`MODIFIED_AFTER`).
- [ ] **sktorrent.eu comparison tool** (original plan item 4): re-scrape catalog,
      compare with the library (size, metadata), test torrent quality.

## Audiobookshelf

- [ ] **Notify ABS after shelving** — `POST /api/watcher/update` for files answered
      "No important changes" (2026-10-09); find out why, else a nightly scan of the
      libraries touched that day. Keep the ABS watcher on for hand copies.
- [ ] **Narrators**: after the Fiction scan, re-run `scripts/abs_narrators.py`
      (fix spellings the scan may have reverted); user review of 132 deletes and
      187 single names (`eBOOKs/abs_narrators_proposal.tsv`).
- [ ] **Stopy, fakta, tajemství collection layout** conflicts with the ABS item
      model (ABS sees the whole program folder as one book, ignores new episodes).
- [ ] **audiobiblio as an ABS custom metadata provider** (Czech radio +
      mluvenypanacek metadata straight into Match / quick-match).
- [ ] ABS collections/series built by audiobiblio (e.g. per program).
- [ ] Raise `fs.inotify.max_user_watches` on the NAS if the watcher misses folders.

## Crawl

- [ ] AUTO (book) crawl: skip re-upserting unchanged known episodes (each run
      rewrites provenance observed_at — slow, lock pressure).
