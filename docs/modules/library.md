# library — Canonical episode/work ownership, post-download pipeline, and ABS integration

**Layer:** Layer 3 of 5 (same tier as `acquire`). May import from `core`, `tags`, `sources`, and `dedupe`. Nothing in `acquire` is permitted to import `library` without a parked violation exception (see [decisions/phase2-import-linter-ignored-violations.md](../decisions/phase2-import-linter-ignored-violations.md)).
**Standalone use:**
- `uv run audiobiblio ingest-url --url URL` — inspect, classify, and queue a single URL
- `uv run audiobiblio ingest-program --url URL` — multi-source discovery + ingest for a whole program
- `uv run audiobiblio add-episode ...` — upsert Station→Program→Series→Work→Episode manually
- `uv run audiobiblio demo-ingest-episode` / `uv run audiobiblio demo-mark-audio-complete` — development fixtures
- `uv run audiobiblio backfill-mediainfo [--limit N] [--dry-run]` — populate bitrate/channels/sample_rate/codec/container on COMPLETE audio assets with NULL bitrate
- `uv run audiobiblio verify-files [--limit N] [--fix]` — detect missing asset files and optionally mark them as MISSING (dry-run by default)
- `uv run audiobiblio sync-tags [--episode-id N | --limit N] [--write]` — compare DB-resolved metadata to file tags and optionally rewrite files (dry-run by default)
- `uv run audiobiblio enrich-from-meta [--limit N] [--dry-run]` — backfill episode title/description/duration from downloaded .info.json files (fallback-titled episodes first)
- `uv run audioloader` — standalone legacy loader entry point

## Responsibilities

- Upserts the full DB hierarchy (`Station → Program → Series → Work → Episode → EpisodeAlias`) from any discovered URL or manual input; re-air detection and aliasing built in.
- Plans download jobs for each episode: creates `Asset` rows, creates `DownloadJob` rows with `PENDING` or `APPROVAL` status (approval required for the first three downloads of a new program).
- Post-download pipeline: call `tags.tag_audio()` → move file to the canonical library path → write `abs_metadata.json` → write `.nfo` sidecar when all episodes in a Work complete.
- Builds canonical file paths (`{Program} ({StationCode})/{Author} - ({year}) {Album} - {NN} {Episode}.m4a`); no year subdirectories.
- Scrapes reference episode catalogs from Wikipedia episode tables and mluvenypanacek.cz for completeness tracking.
- Generates per-program gap reports: compares the scraped catalog against downloaded episodes and flags missing ones.
- Exposes an Audiobookshelf API client that can trigger library scans and list items.

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `upsert_from_item` | `(session, *, url, item_title, series_name, author, uploader, …) -> tuple[Episode, Work]` | Upsert full hierarchy; alias + re-air detection |
| `queue_assets_for_episode` | `(session, episode_id, approval_mode=None) -> list[DownloadJob]` | Delegate to `plan_downloads`; returns new jobs |
| `plan_downloads` | `(session, episode_id, approval_mode: ApprovalMode | None = None) -> list[DownloadJob]` | Create missing Asset rows + DownloadJob rows; AUTO→PENDING, REVIEW→APPROVAL, None→threshold |
| `mark_asset_complete` | `(session, episode_id, asset_type, file_path, …)` | Mark an Asset as COMPLETE |
| `ensure_assets_for_episode` | `(session, episode_id) -> list[Asset]` | Upsert required Asset rows (AUDIO, META_JSON, WEBPAGE) |
| `tag_audio` | `(path, ep, work, force=False)` | Write metadata tags to a downloaded file; tracknumber is always a plain integer (no total); episode title written to `©nam` whenever it differs from the album title |
| `read_media_info` | `(path: Path) -> MediaInfo` | Read technical audio metadata (duration_ms, bitrate, channels, sample_rate, codec, container) from a file via mutagen; returns all-None on any error, never raises |
| `apply_media_info` | `(session, asset, path: Path) -> MediaInfo` | Write MediaInfo fields to Asset row + episode.duration_ms if NULL; commits session |
| `verify_asset_paths` | `(session, limit: int | None = None, fix: bool = False) -> FileCheckReport` | Verify COMPLETE asset file_path existence; optionally mark missing ones as MISSING and stash path in `extra["last_known_path"]` |
| `compute_resolved` | `(session, episode: Episode) -> dict[str, str]` | Compute resolved DB-provenance value for each sync field (title/author/narrator/genre/description/year); falls back to ORM values where no MetadataValue rows exist |
| `sync_episode_tags` | `(session, episode: Episode, write: bool = False) -> SyncReport` | Compare DB-resolved values to file tags; records FILE observations; returns SyncReport with per-field diffs and actions ("none" / "record_file" / "rewrite"); applies rewrites only when write=True. **Note:** For M4A/M4B/MP4 files, requires exiftool to read standard tags (title/artist/date/comment); without it, sync is skipped to prevent overwriting file-side edits with empty DB values. |
| `enrich_episode_from_meta` | `(session, episode, *, dry_run=False) -> EnrichReport` | Read back a COMPLETE META_JSON (.info.json) asset and apply richer title/description/duration/episode_number to the episode ORM row; SCRAPED provenance recorded for all surviving candidates; MANUAL protected; generic titles (is_generic_title) skipped; title updated when current is fallback-pattern or candidate is longer; tolerant of missing/malformed JSON |
| `postprocess_episode` | `(session, episode_id, audio_path) -> Path | None` | Full post-download pipeline |
| `move_to_library` | `(src, ep, work, info=None) -> Path` | Move file to canonical library path |
| `build_paths_for_episode` | `(ep, work=None, info=None) -> dict` | Compute `{"base_dir": Path, "stem": str}` |
| `build_canonical_filename` | `(ep, work) -> str` | Canonical stem without extension |
| `scrape_catalog` | `(program_id, source, url) -> list[dict]` | Scrape episode catalog from Wikipedia or mluvenypanacek.cz |
| `upsert_catalog` | `(session, program_id, entries, source, source_url=None) -> dict` | Insert/update `CatalogEntry` rows |
| `gap_report` | `(session, program_id) -> dict` | Compare catalog vs downloads; list missing episodes |
| `work_completeness` | `(session, work) -> Completeness(have, expected, missing_numbers)` | Count COMPLETE audio episodes vs expected_total; missing_numbers when numbering trustworthy (≥80 % distinct positive episode_number) |
| `incomplete_works` | `(session, limit=100) -> list[tuple[Work, int]]` | Works with expected_total set and have < expected_total; sorted by gap ascending |
| `completed_works` | `(session, limit=100) -> list[tuple[Work, int]]` | Works with expected_total set and have >= expected_total — eligible for finalization; sorted by title |
| `count_incomplete_works` | `(session) -> int` | Lightweight count for console badge |
| `plan_finalize` | `(session, work, library_dir) -> list[str]` | Human-readable dry-run action list for `finalize_work` |
| `finalize_work` | `(session, work, library_dir, dry_run=True) -> FinalizeReport` | Move all COMPLETE asset files (+ same-stem sidecars) into a per-work subfolder; updates `Asset.file_path`; moves only, never deletes; collision → `-2`/`-3` suffix; `flush()` before every move for session consistency (not a hard crash-safety guarantee — partial disk/DB divergence on mid-loop failure is recoverable via import-scan) |
| `trigger_library_scan` | `(library_id=None) -> bool` | POST to ABS scan endpoint |
| `get_library_items` | `(library_id) -> list[dict]` | List items from an ABS library |
| `scan_directory` | `(session, root: Path, scan_id: str, inbox: bool = False, limit: int \| None = None) -> ScanReport` | Walk root recursively; match each audio file against DB episodes in four tiers (dead-path recovery → title match → duplicate check → unknown); persist `ImportFinding` rows; idempotent (updates "new" rows, leaves resolved untouched). |
| `accept_finding` | `(session, finding: ImportFinding, move: bool = False, library_dir: Path \| None = None, trash_fn=None) -> list[str]` | Link file to episode as AUDIO asset (repair MISSING or create new); record FILE provenance; apply_media_info; optionally move to library path. DUPLICATE accept requires trash_fn or raises ValueError. |
| `ignore_finding` | `(session, finding: ImportFinding) -> None` | Mark finding status "ignored". |
| `parse_stem` | `(name: str) -> dict` | Parse filename stem per NAMING_CONVENTION patterns 1–6; returns dict with any of {author, year, album, track, title, performer, publisher}; returns {} for unparseable stems. |
| `propose_segmentation` | `(session, program) -> SegmentationProposal` | Pure analysis (no writes/files/network): parse episode titles with the Czech author-prefix pattern, strip part-markers (shared `_CZECH_PARTS` ordinals from `tags/diacritics.py`), cluster serialized books by (author, book_key), classify anthology/magazine per-episode; mode = majority signal; generic/fallback titles (`is_generic_title`, `^Episode \d+$`) → `unassigned`; confidence 1.0 (multi-part cluster) / 0.9 (author-prefix) / 0.7 (magazine) |

## Files

| File | Purpose |
|---|---|
| `pipelines/ingest.py` | `upsert_from_item()`, `queue_assets_for_episode()`, alias + re-air handling |
| `pipelines/checks.py` | `plan_downloads()`, `mark_asset_complete()`, `ensure_assets_for_episode()`, approval logic |
| `pipelines/postprocess.py` | `tag_audio()`, `move_to_library()`, `postprocess_episode()`, `rename_audio()` |
| `pipelines/library.py` | `build_paths_for_episode()`, `build_program_folder()`, `work_dir()`, `episode_file()` |
| `pipelines/gaps.py` | `gap_report()` — catalog vs downloaded comparison (CatalogEntry-based, program-level) |
| `pipelines/completeness.py` | `work_completeness()`, `incomplete_works()`, `completed_works()`, `count_incomplete_works()` — Work-level completeness against expected_total |
| `pipelines/finalize.py` | `finalize_work()`, `plan_finalize()`, `FinalizeReport` — per-work folder finalization; explicit-only, preview-first, moves-only |
| `pipelines/html_scraper.py` | `scrape_episode_html()`, `build_comment()` — parse saved HTML for extra metadata |
| `pipelines/exporters.py` | `export_abs_metadata()` — write `metadata.json` for ABS |
| `catalog.py` | `scrape_catalog()`, `upsert_catalog()` — Wikipedia + mluvenypanacek.cz scrapers |
| `abs_client.py` | `trigger_library_scan()`, `get_library_items()` — ABS API client |
| `mediainfo.py` | `read_media_info()`, `apply_media_info()`, `MediaInfo` frozen dataclass — mutagen-based quality field population |
| `filecheck.py` | `verify_asset_paths()`, `FileCheckReport` frozen dataclass — file path reconciliation after disk reorganization |
| `sync.py` | `sync_episode_tags()`, `compute_resolved()`, `SyncReport` / `FieldDiff` frozen dataclasses — DB-resolved provenance projected onto audio file tags |
| `importer.py` | `scan_directory()`, `accept_finding()`, `ignore_finding()`, `parse_stem()`, `ScanReport` — import scanner; four-tier matching; `ImportFinding` persistence and resolution |
| `enrich_meta.py` | `enrich_episode_from_meta()`, `EnrichReport` — reads .info.json and backfills episode title/description/duration/episode_number with SCRAPED provenance |
| `segmentation.py` | `propose_segmentation()`, `ProposedWork` / `SegmentationProposal` frozen dataclasses — program-level episode-title analysis proposing per-book works (ADR 0003); pure read-only |
| `audioloader.py` | Legacy `audioloader` entry point |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 2:** Inbox view — per-episode approval UI; currently only the API endpoint exists.
- **Phase 4 Task 6 — Done:** Import scanner: `scan_directory()` in `importer.py`; four-tier matching (dead-path recovery, title, duplicate, unknown); `ImportFinding` table; `accept_finding()` / `ignore_finding()` resolution; `parse_stem()` for NAMING_CONVENTION parsing; `inbox_dirs` config field.
- **Phase 4 Task 5 — Done:** DB ↔ ID3 sync scan with field-by-field provenance diff — `sync_episode_tags()` in `sync.py`; CLI `sync-tags` command.
- **Phase 5 Task 4 — Done:** Work completeness — `expected_total` / `expected_source` on `Work`; `completeness.py` module; `PATCH /api/v1/works/{id}`; `/gaps` page; gap-fill priority (episode.priority=10, "gap-fill" in job reason); console badge.
- **Phase 5 Task 7 — Done:** Finalize complete work into per-work folder — `finalize_work()` / `plan_finalize()` in `pipelines/finalize.py`; `completed_works()` in `pipelines/completeness.py`; `POST /api/v1/works/{id}/finalize` (404/409 guards, default `dry_run=true`); preview-first UI on episode detail and `/gaps` "Ready to finalize" section. Explicit-only — never runs automatically. Target: `{library_dir}/{Program (StationCode)}/{Author} - ({year}) {Album}/` via `build_program_folder()` (extracted from `build_paths_for_episode()`, one shared code path).
- **Phase 5:** `WANTED` records for missing episodes; cross-source gap hunting `[deferred: phase 5+]`.
- **Phase 6 Task 1 — Done:** Segmentation engine (propose) — `propose_segmentation()` in `segmentation.py`; resolves ADR 0003 (works are program-level): serialized / anthology / magazine modes from title patterns; proposals only, nothing applied. Future signal: META_JSON `series` provenance as book_key (series not recorded today; engine must not read files).
- **Phase 6:** Full absorption of `scripts/abs_*.py` standalone scripts; ABS push triggered automatically after every successful postprocess.
