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
- [ ] **Music share lives on a DIFFERENT host: `//10.44.20.10/music`** (unas;
      reachable from nasx via 10.45.0.1, SMB 445 + NFS 2049 open, ~14 ms). Waiting
      for the user: NFS export for 10.45.0.105 or an SMB service account. Then a DSM
      boot-task mount (e.g. `/volume1/unas-music`) + bind into the container as
      `/media/music`. (Mac mount
      `/Volumes/music`), not on nasx — audiobiblio (on nasx) needs access first
      (SMB mount into the container or a sync step). Target root
      `music/mujrozhlas.cz/<Program> (<station>)/`.
- [ ] **All music programs → music share, same approach as Oldies** (numbering,
      `YYYYMMDD`, Plex tags, song identification). State 2026-10-09:

      | Program | music share | audiobiblio |
      |---|---|---|
      | Hudební vzpomínky (CRo) | 179 files, 6.1 GB | 387 eps, 0 downloaded |
      | Polední koncert (CRo3) | 21 files, 2.1 GB | 521 eps, 0 downloaded |
      | Koncerty Dvojky (CRo2) | 90 files, 1.2 GB | 97 eps, 0 downloaded |
      | Sedmé nebe (CRo3) | 16 files, 1.1 GB | 680 eps, no crawl target |
      | Vltavské speciály (CRo3) | 16 files, 1.0 GB | MIXED (user 2026-10-09): classify PER EPISODE by context — literature → fiction book shelf, music → music share. Signals: rAPI genres/keywords, title/perex ("četba", "hra" vs "koncert", "hudba"), mluvenypanacek category, duration |
      | Folkový antikvariát (CRo) | 62 files, 1.0 GB | program known, 0 eps |
      | Vánoční den Euroradia 2025 | 984 MB (no m4a/mp3) | not in DB |
      | Písničky z cizí kapsy | ~empty | 206 downloaded (CRoCB) in the audiobooks working library |
      | Folki — https://www.mujrozhlas.cz/folki | — | 196 downloaded in the audiobooks working library |
      | Hudební svět Michala Horáčka | ~empty | target, 0 eps |
      | Futurissimo (CRo3) | empty | 289 eps, 0 downloaded |
      | Hudební rebelové (CRo+) | empty | not in DB |

      Merge what exists on both sides (dedupe by length), move audiobiblio's
      downloads out of the audiobooks library, keep these out of ABS.
- [ ] **Song identification → split into tracks.** rozhlas publishes no tracklists
      for Oldies. Option A (free) first: speech/music segmentation
      (e.g. inaSpeechSegmenter) → Chromaprint/AcoustID → MusicBrainz; trial on 3
      episodes, measure the hit rate. Option B (paid) AudD / ACRCloud only after A.
      Shazam: no official API for this — no.

## Library & shelving

- [ ] **Librarian robot on nasx** (user 2026-10-09: "instead of asking AI to do these steps…
      prepare a nasx running robot"). Scope: `eBOOKs.temp`, `eBOOKs.temp2sort`,
      `eBOOKs.temp2sort2025`, `eBOOKs.temp2sort.ZV`, `2sort` (hundreds of GB; July
      inventory: ~10.8k dirs / 1.93 TB) + 80 GB of downloads on the Mac (copy to a NAS
      staging dir first). Pipeline: inventory (files, measured length, tags; incremental)
      → identify (DB, mluvenypanacek, rAPI, databazeknih; title+author+length) → decide
      (same recording vs version, completeness, target library) → act when confident
      (shelve with mluvenypanacek tags, cover, ABS metadata.json; duplicates →
      #recycle; journal, reversible) → uncertain into one bulk review page.
      **Phase 1 = dry-run report only.**
- [ ] **Barbora Haplová cleanup**: kids works → `4kids` (Kapitán Kiking 23232, Cesta do
      švábího ráje 2869, Josef Rosol a Konrád Slíž 23347/25317, Chibyčky 23441/25028,
      Mechovky a trilobiti 2874), Podvodníci 23701 (fantasy serial, family); confirm on
      mluvenypanacek, tags incl. genre "rozhlasova hra; pro deti a mladez", covers (rAPI /
      user's jpg / generated), dedupe by length, merge 3 ABS authors into "Barbora Haplova",
      set author on all audiobiblio works.
- [ ] **ABS "AIO" library** over `eBOOKs.fiction` + `4kids` (family search across both) —
      caveat: items appear twice, progress tracked per library. Awaiting user decision.
- [ ] **ABS narrator via metadata.json** (not the composer tag — composer = author of music).
- [ ] **Stopy, fakta, tajemství**: analyse user's copies in `eBOOKs/mujrozhlas/Stopy, fakta,
      tajemstvi (CRo2)` and `eBOOKs/audiobiblio`, finish organizing the program.
- [ ] **Cover generation** when no source exists (Gemini image model; key via the luhacovice
      gateway).

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
- [ ] **"Finalizovat" button reports OK when nothing moved** (Den trifidů 2026-10-09:
      stale paths of parts 1–10 → "Missing on disk", 0 moves, HTTP 200, no final_path).
      Must say clearly "nothing moved — N files missing" and offer the path repair.
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
  - [x] Monthly sync (scheduler, 1st of the month 02:15, changes since the last
        sync — `audiobiblio/sources/mluvenypanacek.py`).
  - [ ] **Remember the user's conflict verdicts** — a resolved conflict (e.g. Staré
        pověsti 2009 vs catalog 2008) must not come back every month; store the
        verdict per (work, field, catalog value) and learn from it.
  - [ ] **Corrections for mluvenypanacek.cz** — errors we find go to
        `eBOOKs/panacek_corrections.tsv` (record, field, their value, correct, evidence);
        sending them to the site is the user's call.
  - [ ] Parser: lengths written next to the premiere (`(Olomouc, 10:04 h.; 24 min.)`)
        — length coverage is only ~13 %; it is the key to telling versions apart.
  - [ ] Monthly analysis right after the sync (user rule 2026-10-09): enrich ID3
        tags from matched records, list missing episodes and missing works.
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
