# Playbook: program recovery (finished or expired program)

Goal: for every program we track, the DB knows ALL its works (title, author,
summary, air date, credits) even after the audio expired; owned copies are
linked; missing ones get external candidates; finished programs leave the
daily crawl.

## Detectors (read-only)

| ID | Signature | Meaning |
|----|-----------|---------|
| D1 | program.station = `mujrozhlas` while a `<station>.rozhlas.cz` page exists for it | wrong station → wrong genre tag `Name (mujrozhlas)` instead of `Name (CRo2)` |
| D2 | active target, `url` on mujrozhlas.cz, `paired_url` empty, REVIEW mode | only the rAPI path runs: expired shows index nothing, ever |
| D3 | active target crawled daily, 0 episodes in its program | silent empty crawl (D1/D2, or URL changed) |
| D4 | active target, interval < 1000 h, newest episode older than 1 year, nothing AVAILABLE | finished program → archive (interval 2400 h ≈ 100 days) |
| D5 | GONE episode without COMPLETE audio whose author+title matches an ABS item (unidecode, case-insensitive) | owned copy not linked |
| D6 | GONE episode, no owned copy | external search candidate (panacek catalog, webshare, other programs' re-airs) |

## Procedure

1. **Station + pairing** (D1, D2): program → station of the rozhlas.cz site,
   program url → station page; target url → station page, `paired_url` →
   mujrozhlas show; name `Program (CODE)`. AUTO mode for drama/book programs
   (full crawl incl. station-page HTML archive; REVIEW only reads live rAPI).
2. **Crawl once** (`crawl_target`): station page archive becomes GONE
   episodes with author, title, summary, air date. No jobs are queued for GONE.
3. **Archive** (D4): `interval_hours = 2400`, `next_crawl_at = now + 100 d`.
4. **Link owned copies** (D5): identity is confident when two sources agree:
   - file tag comment == station-page summary, or
   - cast/narrator named in the summary appears in the file tags, or
   - panacek `recorded_years` + `total_minutes` match the file (±2 min).
   Confident + curated shelf directory → `POST /api/v1/works/{id}/adopt`
   (dry-run first; sets final_path = tag protection). Single files in Kids
   (`/media/ebooks/4kids`) — adopt does not accept them yet (see Gaps).
   One source only → review list.
5. **Credits** (all): panacek record (recording year, director, music,
   dramaturgy, cast with roles) → enrichment proposals, never overwrite
   manual values.
6. **External candidates** (D6): webshare `POST /api/search/` (anonymous),
   match on title + author + plausible size (radio play ≈ 0.5 MB/min at
   64 kbps); panacek year disambiguates productions. Candidates are listed
   for the user; download only after approval.
7. **Duplicates**: the same production on two shelves (Fiction + Kids) →
   duplicate review, nothing deleted.

## Gaps (to build so the agent can run this unattended)

- [ ] adopt for single files outside curated dirs (Kids `4kids/*.m4a`).
- [ ] D1–D6 as a `audiobiblio playbook scan program-recovery` CLI with a
      JSON report and counts on /system.
- [ ] panacek match → credits enrichment for archive episodes.
- [ ] webshare candidate table (ident, name, size, matched work, verdict).

## Cases

### 2026-11-03 — Rozhlasova hra pro celou rodinu (CRo2)
- Program 1161 / target 1451 was created from the mujrozhlas URL: station
  `mujrozhlas`, no pair, REVIEW → daily crawl returned 0 for months (D1–D3).
- Fixed: station CRo2, url https://dvojka.rozhlas.cz/rozhlasova-hra-pro-celou-rodinu-7230773,
  paired mujrozhlas, AUTO; crawl indexed 11 plays (2014–2019, GONE, with
  summaries); archived (next check 2027-02-11).
- Owned: Marco Polo (Rösner in summary = tag comment; panacek 1990) and
  Bakuv pribeh (tag comment = summary) adopted from Fiction. Velka huba a
  maly kun (panacek 1981, 39 min = file 39.3 min) — Kids single file, adopt
  gap. Pollyanna in Temp: panacek has 2014 (Mančal) and 1946 productions,
  file 51.7 min — probably 2014, user check.
- External: webshare `Frances Hodgson Burnettová - Malý lord (1999).m4a`
  28 MB = panacek 1999, 56 min → strong; `Neilson Anthony - Lisa v říši
  kupodivů.rar` 49 MB = panacek 2018 → strong. Zambon, Hudsky, Panakova,
  Hejna, Hejnic: catalogued in panacek, not on webshare.
- Learned: the user's own webshare search found Malý lord first → webshare
  search belongs in the standard procedure; mujrozhlas-only REVIEW targets
  are blind to expired shows.
