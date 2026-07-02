# 0002 — Monolithic per-site download scripts

**What we tried**

13 versions of `download_sites_v*.py` (Aug 2025) tried to build a single
Python script that: read a URL list from `websites_mujrozhlas.json`, crawled
each site with `requests`, downloaded audio via `yt-dlp`, and wrote tags —
all in one flat file with no persistent state. The series ran from a bare
loop with random sleep (`v20250809x13`) through increasingly complex
per-series status tables, `episodes_db.json` for rudimentary deduplication,
and eventually Phase-1/Phase-2 separation (`v20250815.01`).

**Why it failed**

Without a real database there was no reliable way to track what had already
been downloaded across runs: the JSON flat file was overwritten, duplicate
downloads happened, and crash recovery required manual inspection. The script
mixed discovery, download, and tagging into one sequential pass — a 429 or
network drop mid-run left partial state with no way to resume. When the series
was split across multiple channels the per-URL loop could not merge them. The
approach also had no queue, so adding a second source required forking the
whole script. The live replacement (`sources/discovery.py` + `acquire/`)
stores every episode in SQLite with explicit `JobStatus` transitions and allows
incremental runs.

**Don't retry unless**

You have only one source site, only tens of episodes, and no need for crash
recovery or deduplication — e.g., a one-shot archive dump. Even then, prefer
yt-dlp's built-in `--download-archive` over hand-rolled JSON state.

**Where the code was**

`archive/download_sites_v20250809x13.py` through
`archive/download_sites_v20250815.01.py` (14 download_sites files, deleted with
`archive/` in commit `docs: mine archive/ into dead-ends + decisions, delete archive`).
