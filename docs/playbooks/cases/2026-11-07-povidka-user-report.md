# Case 2026-11-07 — user report: Povídka (CRo3) downloads broken

Reported by the user with reference downloads (JDownloader, ad-free, full) in
`~/Downloads/jdownloads/`. Read-only investigation; nothing changed yet.

## A — http://nasx:8321/works/119016 "Dokumentární povídky Miloše Doležala, Aleny Wagnerové a Josefa Kopty"
Correct source: https://vltava.rozhlas.cz/dokumentarni-povidky-milose-dolezala-aleny-wagnerove-a-josefa-kopty-9652973 (5 parts).

F-A1 — 8 episode records for 5 parts: parts 3, 4, 5 exist twice, created by
two discovery paths ~10–120 min apart: `show-sweep` (rAPI uuid ext_id,
diacritic title) and the crawl/gap-fill path (numeric yt-dlp ext_id, ascii
title). Both point to the same stream url and the same file path → each file
was downloaded twice, the second overwrote the first.
I-A1 — episode identity is the ext_id only; the two paths use different ids
for the same audio. Dedupe needed on (work, episode_number) / stream url /
rAPI uuid ↔ numeric id mapping.

F-A2 — part 2 "Bohouš svině" (ext_id 12151052) was not missing from the
DB: it is episode 2350 in work 1531 under program 69 "Radiokniha (CRo2)",
created 2026-07-23 (a 2019 Dvojka airing of the same recording) and
downloaded there 2026-11-04 as `/media/audiobooks/Radiokniha (CRo2)/Milos Dolezal - (2019) … Bohous svine - 01.m4a`.
I-A2 — a re-aired part keeps its first work (ext_id unique) → it never
joins the new series. A part belongs to EVERY series that airs it; the new
series needs a link/alias, not a missing gap.

F-A3 — the work shows as complete: `complete_audio_count` counts distinct
episode IDS with complete audio (8) ≥ expected_total (5). Part 2 is absent.
I-A3 — BUG: completeness must count distinct episode NUMBERS 1..N.
(`library/pipelines/completeness.py:28`, used by views.py:825, works.py:307.)

F-A4 — part 1 is a different variant: 1552.7 s at 392 kbps vs reference
1536.4 s at 194 kbps (+16 s = ad/teaser variant). Parts 3–5 match the
reference durations to the ms (rAPI variant).
I-A4 — the yt-dlp/gap-fill path picks the ad variant; the rAPI path picks
the clean one. Prefer rAPI audio; known rule (memory: ±30 s ad variants).

F-A5 — episode url = `https://croaod.cz/stream/<uuid>.m4a/playlist.m3u8`,
tag `www` = stream url + `;https://vltava.rozhlas.cz/povidka-5057586`
(program page). Neither is the article url.
I-A5 — violates the rule "episode-level URLs only". The show-sweep stores
the stream url as the episode url.

F-A6 — tags: GENRE `audiokniha`, publisher `mujrozhlas`. Program 1790 is
`povidka` on station `mujrozhlas` (genre empty), twin program 1791 `povidka`
on station `CRo-portal`; target 54 is named `povidka` (lowercase, no code).
Files land in `/media/audiobooks/povidka (mujrozhlas)/`.
I-A6 — expected genre "Povidka (CRo3)" + "povidka". Same D1 pattern as
Rozhlasova hra: program on the wrong station → wrong genre/folder.
"audiokniha" is a default genre when the program has none.

F-A7 — file names truncate the work title to different lengths per part
("Dokumenta - 03 …", "Dokumentarni povidky Milose Do - 05 …").
I-A7 — length cap applied to work title after the part title; the prefix
must be stable across parts.

## B — http://nasx:8321/episodes/142671 "Jaromír Tomeček: Vlkodlak"
Correct source: https://vltava.rozhlas.cz/vlkodlak-a-touha-dve-povidky-jaromira-tomecka-9643143 (2 parts).

F-B1 — episode sits in work 26764 "Povídka" (author `Dominik Macas`,
expected_total 7) with 93 episodes of ~15 different books (Epstein,
Anderson, London, Wells, Bass…), program = catch-all `mujrozhlas`
(folder `/media/audiobooks/mujrozhlas (mujrozhlas)/`).
I-B1 — work = program, not book (D7 catch-all, cf. Toulky case F5–F6).
The author of one old episode became the author of the whole "work" and
is written into every file of it.

F-B2 — downloaded 1730.3 s vs reference 1715.2 s (+15 s ad variant).
I-B2 — same as I-A4.

## C — Povídky klasiků https://vltava.rozhlas.cz/povidky-klasiku-9584940 not downloaded
F-C1 — target 214 exists, active, crawled daily, but `approval_mode=REVIEW`
→ its jobs wait in the APPROVAL queue forever. The original AUTO target 1
(`mujrozhlas.cz/povidky-klasiku`) was deactivated 2026-07-23.
I-C1 — book programs must be AUTO. The approval queue (92,860 episodes)
likely hides more book programs → list REVIEW targets that are book/drama
programs (finishing-pipeline list of 19 book programs) and switch them.

## Fix plan (proposed, not applied)
1. completeness by distinct episode_number (test first) — stops false "complete".
2. dedupe parts across discovery paths + prefer rAPI (clean) audio.
3. re-aired part joins the new series (alias), not stays only in the old work.
4. article url as episode url (show-sweep).
5. program station/genre fix for Povídka (CRo3) + REVIEW→AUTO for book programs.
6. data repair of works 119016 and 26764 (journaled; files from the user's
   reference downloads may replace ad variants — user decides).

## Applied 2026-11-07 (deployed)
- 8fdd03d completeness counts parts, not records (5 works were falsely
  complete: e.g. Hejkalova "Dum pod namestim" 8/10, Pilatova "Tsunami blues" 12/13).
- 01bcb13 librarian author gate: episode title / article url slug confirm the
  author. Dry run: 23 works released (19 Povidky klasiku, Havel x3, Hugo), all
  correct; redaktor bylines (Macas, Hyncikova) still wait.
- Povidky klasiku target 214 REVIEW→AUTO: 14 parked episodes released to download.
  Correction of an earlier claim: all 46 archive stories ARE in the DB
  (program 101); 43 downloaded, but only ~20 shelved (rest blocked by the gate).
- f6a0c03 + 8078ec2 (agent): episode url = page (rapi/view/episode/<uuid>, NOT yet
  verified in a browser — mujrozhlas answers 403 to scripts), stream kept as alias;
  re-aired part joins its new serial (Work.extra.shared_parts), placed as a COPY.
- 118fa5b (agent): one audio = one episode across rAPI and crawler paths;
  downloader never overwrites another episode's file.
- 2e4b9d5 tightened the part+duration rule after the production scan: as
  written by the agent it paired 97,788 episodes (weekly shows, part 1,
  exactly 55:00) and would have swallowed new shows. Live ~15 min, 0 merges.

## Found during verification
F-D1 — 4,507 episode pairs share one file path, mostly "Oldies jako na dlani"
(CRo2) and "Folklorni notovani": weekly shows grouped into one work, every
show numbered 1 → `… - 01.m4a` → each week overwrote the previous file.
I-D1 — naming must make each show unique (air date or episode title) when
part numbers collide. Until fixed, new shows are SKIPPED by the overwrite
guard instead of destroying the previous file. NEXT FIX.
Lesson — every new matching rule is run against the production DB
(read-only) BEFORE deploy, and the pair count sanity-checked.

## Escaped-content audit (agent, read-only) and rescue — 2026-11-07 afternoon
Audit: 358k AVAILABLE episodes without audio on REVIEW targets (92k APPROVAL +
265k index-only rapi-crawl rows with no job, nothing reports them); 14/30 BOOK
and 18/32 DRAMA REVIEW targets have 0 episodes (their urls are mujrozhlas
CATEGORY pages, never resolve); ~82 not-downloaded live episodes expire in
30 days, almost all books/plays; D-dur weekend blocks (Šostakovič) are
broadcast-only (no audio in rAPI) — only a live-stream recorder could catch them.

Root causes found from it (all fixed, tested, deployed):
- 9057c40 serial sweep: SQLite lower() folds ASCII only → "Četba…" never
  matched '%četb%'; Četba na pokračování (Dvojka) and Četba s hvězdičkou
  were never swept. Rerun: 6 live serials, 42 parts (Baarová 5, Ke dnu 10,
  Jeden život 7 — expiring the same day, Trujkunt Kruhy 8 + Čtverka 8,
  Prosecké peklo 4).
- 1b4774e show sweep: exact lower(name) target→program join skipped 25 of
  93 AUTO targets and matched 19 to twins (cause of "povidka (mujrozhlas)").
  Now url → unaccented name → station host; programs without series get one.
  Rerun: 69 shows resolved, 223 new live episodes, 3,544 known-by-id (no
  fuzzy merges).
Data actions (journaled here):
- 17 high-confidence BOOK/DRAMA targets REVIEW→AUTO: 13, 21, 810, 185, 1269,
  1415, 1520, 1403, 228, 235, 88, 1500, 1319, 264, 1386, 1610, 1259
  (parked APPROVAL jobs released). Music cycles / docu-serials left for the user.
- priority 80 on 100 episodes expiring ≤45 days; 42 index-only ones got jobs
  via plan_downloads(AUTO).
- 4 Vltavské speciály dramas (Ptáček, Liblice, Vrchlický, Masaryk) ingested
  one-off (target url has no node id → sweep skips it), episodes 416902–416905.
Verified: Baarová 5/5 and Ke dnu 10/10 on disk, durations = rAPI to the second.

Open:
- monitoring/alerts (0-episode targets after 3 crawls: 63 non-news now;
  expiring ≤14 d not downloaded; librarian stuck) — NOT built yet.
- file names double the author ("Adam Georgiev - Adam Georgiev Denik…"),
  also on Fiction shelves ("Konstantin Biebl - Konstantin Biebl …").
- 30 category-url targets → replace with real shows; 16 AUTO targets still
  unmatched (Toulky program on station mujrozhlas, Věda Plus page gone…).
- weekly-show file collisions (Oldies) — naming.
- rapi/view/episode/<uuid> link: user to verify in a browser.
