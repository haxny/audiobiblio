# 0004 — exiftool-driven tag analysis (tags_analyze_folder series)

**What we tried**

`archive/tags_analyze_folder_v2025081201.py` through
`archive/tags_analyze_folder v20250814v003.py` (10+ versions, Aug 2025)
used `exiftool -j -G1` as the sole metadata reader. Each version produced
a JSON suggestions file for human review, with progressively richer outputs
(encoding detection, mojibake warnings, narrator extraction from Comment field,
M3U playlist ordering, color-coded reports).

**Why it failed**

Shelling out to `exiftool` for every folder was slow and added a non-Python
system dependency that complicated deployment. The suggestions-file workflow
(preview JSON → human edits → apply separately) required two manual steps and
had no way to batch multiple folders. The approach was also stateless: running
it twice on the same folder produced a new suggestions file, discarding any
manual edits to the previous one. By v20250818 the logic was re-implemented
in `tag_fixer.py` using mutagen directly, eliminating the exiftool dependency
for reads while keeping it only for edge-case formats.

**Don't retry unless**

You need to read formats that mutagen does not support (e.g., WMA, exotic
container types). In that case, keep exiftool as a read-only fallback for
unknown extensions only, not as the primary tag reader.

**Where the code was**

`archive/tags_analyze_folder_v2025081201.py` through
`archive/tags_analyze_folder v20250814v003.py` plus `archive/tags_analyze_folder.py`
and `archive/tags_analyze_folder.txt` (deleted with `archive/` in commit
`docs: mine archive/ into dead-ends + decisions, delete archive`).
