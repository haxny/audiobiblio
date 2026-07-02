# 0001 — audioloader v003: type-system break on Path vs str

**What we tried**

`audioloader_v20250910v003_broken.py` attempted to migrate the entire audioloader
from `str`-based paths to `pathlib.Path` in a single step. It also added
inline tag-writing using mutagen directly inside the audioloader (M4A and MP3
branches), and simplified `_find_downloaded_audio` to a single `glob()` call
instead of the careful `[{audio_id}]` bracket-matching logic in v002.

**Why it failed**

The refactor broke the call site of `_finalize_move`: v002 expected
`(src_file: str, src_audio: str, info: dict)` while v003 changed it to
`(src_file: Path, info: dict)` — removing the `src_audio` argument entirely.
Callers still passed two path arguments, causing a `TypeError` at runtime.
The simplified `_find_downloaded_audio` (glob on `ep_id` as bare string) would
miss files whose names use the `[{id}]` bracket convention, regressing a
deliberately defensive lookup. The file was marked `_broken` and v002 remained
the working base.

**Don't retry unless**

You update every call site atomically together with the signature change, AND
add a test that actually downloads a file and asserts the post-move path exists
(so a regression is caught before marking it stable).

**Where the code was**

`archive/audioloader_v20250910v003_broken.py` (deleted with `archive/` in
commit `docs: mine archive/ into dead-ends + decisions, delete archive`).
Stable predecessor: `archive/audioloader_v20250910v002.py`.
