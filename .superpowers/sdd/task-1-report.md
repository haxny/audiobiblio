# Task 1 Report: Segmentation Engine — Pure Analysis

## Status
COMPLETE. 33 new tests pass; full suite 498 passed, 2 skipped (was 465+2).

---

## Files Changed

| File | Action |
|------|--------|
| `audiobiblio/library/segmentation.py` | Created — implementation |
| `tests/library/test_segmentation.py` | Created — 33 tests across 6 classes |
| `.superpowers/sdd/task-1-report.md` | Created — this report |

---

## Pattern-Parsing Decisions

### Author-prefix regex
Used verbatim from the brief:
```
^([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^:]{2,60}?):\s+(.+)$
```
The `[^:]{2,60}?` non-greedy match is key: it stops at the first colon, so
"Zlatý poklad republiky. Kam zmizely státní rezervy?" (no colon) never matches.

### Name guard (looks-like-a-name)
Before treating the author candidate as a real author, we check:
- ≤ 4 whitespace-separated words
- No digits (via `re.compile(r"\d")` match)
This prevents "10. 5. 2024" or "rok 1968" from being treated as an author.

### Part-marker stripping — reuse of `_CZECH_PARTS`
`_CZECH_PARTS` is imported from `audiobiblio/tags/diacritics.py` (not duplicated).
The function `_strip_part_marker` first tries four regex patterns:
1. `(N/M)` or `N/M` at end
2. `N. díl` / `N. část` (with optional parens)
3. `, část <word>` (no numeric ordinal)
4. `- N` at end

If none match, it normalises the rest string via `_ascii_lower()` (NFKD + lowercase,
same logic as how `_CZECH_PARTS` keys were originally derived) and does a suffix
lookup against `_CZECH_PARTS`. This covers ordinal words like "část první" → part 1.

### False-positive guard (SFT documentary titles)
"Zlatý poklad republiky. Kam zmizely státní rezervy?" has **no colon**, so
`_AUTHOR_PREFIX_RE` never matches. Result: `author=None`, signal `"episode_title"`,
mode `"magazine"`. Verified by two explicit test cases.

### Mode determination
`signal_counts.most_common(1)[0][0]` over non-unassigned episodes.
Tie: Python Counter's most_common is stable for equal counts (first inserted wins),
which is acceptable for this analysis layer.

### Confidence rules
| Condition | Confidence |
|-----------|-----------|
| Serialized cluster with ≥ 2 episodes | 1.0 |
| Serialized cluster with 1 episode | 0.9 (same as anthology) |
| Author-prefix, no part marker | 0.9 |
| No author-prefix | 0.7 |

---

## TDD Evidence (RED → GREEN)

**RED**: `pytest tests/library/test_segmentation.py` collected with ImportError
(`ModuleNotFoundError: No module named 'audiobiblio.library.segmentation'`) — 
confirmed before writing implementation.

**GREEN**: After creating `audiobiblio/library/segmentation.py`, all 33 tests pass
in 0.66s.

**Regression check**: Full suite 498 passed, 2 skipped — identical skip count,
zero regressions.

---

## Concerns / Future Notes

1. **Tie-breaking in mode**: when two signals have equal counts, Python Counter
   returns the first-inserted. A stricter tie-break (e.g. "prefer serialized")
   could be added later.

2. **`_CZECH_PARTS` suffix matching** uses a naive `_ascii_lower` that mirrors
   how the keys were originally created, but is not guaranteed identical to the
   full `strip_diacritics()` pipeline in `diacritics.py`. A future refactor
   could expose a shared normaliser.

3. **Meta-JSON series signal** is noted as a future enhancement in the brief
   (skipped per brief instructions: "SKIP this; note as future signal").

4. **Single-episode serialized cluster**: gets confidence 0.9 (same as anthology)
   rather than 1.0, since we cannot confirm it's truly serialized with only one
   part. This is a conservative choice not explicitly specified by the brief.

---

## Post-implementation review (orchestrator pass)

The implementation was produced in an isolated worktree and cherry-picked onto
`feature/phase6-segmentation-abs` (which had one extra commit: the Phase 6 plan
doc). Review fixes applied before finalizing the commit:

1. **Lint (F841)**: unused `eps = _add_episodes(...)` assignment in
   `test_false_positive_guard_zlatý_poklad` — assignment removed.
2. **Sort-key robustness**: serialized-cluster ordering key was
   `(part_num or 9999, e[2] or "")`, which compares `datetime` against `""`
   (TypeError) when two entries share a part number and only one has
   `published_at`. Changed to `str(e[2]) if e[2] else ""`.
3. **Missing docs requirement**: brief line "Suite + lint. Docs: library.md."
   was not fulfilled — added `propose_segmentation` to the public-interface
   table, `segmentation.py` to the files table, and a "Phase 6 Task 1 — Done"
   entry in `docs/modules/library.md`.
4. **Future-signal note**: the META_JSON `series` skip is now recorded in the
   module docstring (brief: "note as future signal"), not only in this report.
5. **Commit message**: normalized to the brief's prescribed message
   `feat: segmentation engine — propose per-book works from title patterns`.

Layer legality confirmed: `docs/modules/library.md` states library (Layer 3)
may import from `core`, `tags`, `sources`, and `dedupe` — both
`tags.diacritics._CZECH_PARTS` and `dedupe.matching.is_generic_title` imports
are downward-legal.
