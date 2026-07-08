# 0003 — Works are program-level for now (segmentation deferred)

**Date:** 2026-07-08 · **Status:** resolved — superseded by Phase 6 segmentation engine

**Resolved by:** `library/segmentation.py` (`propose_segmentation` + `apply_segmentation`) and `segment-works [--program-id N] [--apply]` CLI, merged in Phase 6. First applied at gate: Povídky klasiků (#101) split into 12 per-story Works (10 anthology + 2 magazine episodes); other programs left for user review on `/segmentation`. New episodes in unapplied programs continue to land in the catch-all Work until the user applies segmentation — handled via the review page, auto-routing deferred.

Observed at the Phase 5 gate: all 9 `works` rows in the dev DB are
program-titled — ingest creates one catch-all Work per series/program instead
of one Work per book/audiobook. Consequences: `/gaps` and `expected_total`
count episodes across a whole program, Finalize (§4.6) derives folder names
from the program-level title, and databazeknih matching (§4.4) fuzzy-matches
the program title rather than individual book titles, so per-book enrichment
rarely fires.

We accept this granularity for now: the pipelines are correct at the
granularity they are given, and re-segmenting mid-phase would destabilize the
P5 features that build on Work rows. Planned fix ("work segmentation", next
phase priority): split program-level Works into per-book Works using the
series information already captured in `meta_json` plus title patterns
(e.g. "Author: Title N/M"), migrating episodes and provenance rows to the
segmented Works.
