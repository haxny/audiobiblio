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
