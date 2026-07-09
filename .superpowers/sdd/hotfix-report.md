# Hotfix Report: fix/multipart-dedupe-dup-jobs

## Status

All green. 625 passed, 2 skipped. 18 new tests added.

## Commits

| SHA | Subject |
|-----|---------|
| `88342ab` | fix: distinct-URL entries never fuzzy-collapse (multi-part books) |
| `649edc2` | fix: plan_downloads skips assets with open jobs |
| `0dfa999` | feat: dedupe-jobs cleanup command |

## Bug A — Fuzzy dedupe collapses multi-part episodes

**Root cause**: `seen_titles` stored only `(norm_title → index)`. Tier-3 fuzzy
match fired whenever `SequenceMatcher.ratio() > 0.9`, even when both entries
had distinct URLs — collapsing parts of the same multi-part book into one.

**Fix** (`audiobiblio/dedupe/matching.py`):

- `seen_titles` changed from `dict[str, int]` to `dict[str, tuple[int, str]]`
  storing `(index_in_unique, stripped_url)` alongside each seen title.
- Tier-3 guard: if **both** entries have a non-empty `norm_url_strip_reair` URL
  and those URLs differ → `continue` (skip match, treat as distinct episodes).
- Urlless entries still collapse (guard doesn't fire when either URL is empty).
- Re-air pairs still collapse at tier 2b before reaching tier 3.

**Tier-3 guard one-liner**:
```python
if stripped_url and seen_stripped_url and stripped_url != seen_stripped_url:
    continue
```

**Behaviour change**: cross-host fuzzy-title dedup (two different hosts, same
title) is now suppressed by the URL guard. `test_tier3_fuzzy_title_match` was
updated to use urlless entries to preserve coverage of the urlless-collapse path.

**Tests added**: 6 in `tests/dedupe/test_matching.py`.

## Bug B — plan_downloads creates duplicate open jobs on re-crawl

**Root cause**: `plan_downloads` in `checks.py` created a job whenever
`asset.status in {MISSING, STALE, FAILED}` without checking whether an open
job already existed for `(episode_id, asset_type)`.

**Fix** (`audiobiblio/library/pipelines/checks.py`):

Before creating each job, query for existing PENDING/APPROVAL/RUNNING/WATCH
jobs for `(episode_id, asset_type)`. If found → skip and log.
ERROR excluded from open statuses to preserve retry semantics.

**Tests added** (3): in `tests/library/test_plan_downloads.py`.

## CLI: dedupe-jobs

New command `audiobiblio dedupe-jobs [--dry-run]` in `cli.py`:

- Fetches all open jobs ordered by `id ASC` (oldest first).
- Groups by `(episode_id, asset_type)`; first entry per group is kept.
- Remaining entries → status `SKIPPED`, reason `"duplicate job cleanup"`.
- `--dry-run` prints the report without committing.

**Tests added** (8 + 1 test file): `tests/test_dedupe_jobs_cmd.py`.
