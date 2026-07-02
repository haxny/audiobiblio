# 0006 — Stale test scripts importing removed tag_fixer symbols

**What we tried**

Three test scripts (`archive/test_genre_taxonomy.py`,
`archive/test_rename_preview.py`, `archive/test_rename_apply.py`) tested
functionality of the monolithic `audiobiblio/tag_fixer.py` as it existed
before the Phase 1 module split. They imported private symbols
(`_process_genre`, `_generate_suggestions`, `_apply_changes`,
`_rename_files_and_folder`, `_find_audio_files`, `_read_mutagen_tags`,
`_aggregate_album_tags`, `_sanitize_filename`, `_generate_new_filename`,
`_generate_new_folder_name`) directly from `audiobiblio.tag_fixer`. These
symbols were either renamed, moved into sub-modules (`audiobiblio.tags.*`),
or no longer exist at the same path after the redesign. pytest was already
scoped away from `archive/` so they did not break CI, but they could not be
run.

**Why it failed**

None of the behavior they covered was lost — genre processing lives in
`audiobiblio/tags/genre.py`, rename logic in `audiobiblio/tags/cli.py` and
`audiobiblio/tags/namer.py`. The tests were never converted to import the
new paths and were never added to the test suite under `tests/`.

**Don't retry unless**

If any of the covered behaviors are not yet tested in `tests/tags/`, add
proper pytest tests there importing from the current module paths. Do not
resurrect these scripts.

**Where the code was**

`archive/test_genre_taxonomy.py`, `archive/test_rename_apply.py`,
`archive/test_rename_preview.py` (deleted with `archive/` in commit
`docs: mine archive/ into dead-ends + decisions, delete archive`).
