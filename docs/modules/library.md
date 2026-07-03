# library — Canonical episode/work ownership, post-download pipeline, and ABS integration

**Layer:** Layer 3 of 5 (same tier as `acquire`). May import from `core`, `tags`, `sources`, and `dedupe`. Nothing in `acquire` is permitted to import `library` without a parked violation exception (see [decisions/phase2-import-linter-ignored-violations.md](../decisions/phase2-import-linter-ignored-violations.md)).
**Standalone use:**
- `uv run audiobiblio ingest-url --url URL` — inspect, classify, and queue a single URL
- `uv run audiobiblio ingest-program --url URL` — multi-source discovery + ingest for a whole program
- `uv run audiobiblio add-episode ...` — upsert Station→Program→Series→Work→Episode manually
- `uv run audiobiblio demo-ingest-episode` / `uv run audiobiblio demo-mark-audio-complete` — development fixtures
- `uv run audiobiblio backfill-mediainfo [--limit N] [--dry-run]` — populate bitrate/channels/sample_rate/codec/container on COMPLETE audio assets with NULL bitrate
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
| `postprocess_episode` | `(session, episode_id, audio_path) -> Path | None` | Full post-download pipeline |
| `move_to_library` | `(src, ep, work, info=None) -> Path` | Move file to canonical library path |
| `build_paths_for_episode` | `(ep, work=None, info=None) -> dict` | Compute `{"base_dir": Path, "stem": str}` |
| `build_canonical_filename` | `(ep, work) -> str` | Canonical stem without extension |
| `scrape_catalog` | `(program_id, source, url) -> list[dict]` | Scrape episode catalog from Wikipedia or mluvenypanacek.cz |
| `upsert_catalog` | `(session, program_id, entries, source, source_url=None) -> dict` | Insert/update `CatalogEntry` rows |
| `gap_report` | `(session, program_id) -> dict` | Compare catalog vs downloads; list missing episodes |
| `trigger_library_scan` | `(library_id=None) -> bool` | POST to ABS scan endpoint |
| `get_library_items` | `(library_id) -> list[dict]` | List items from an ABS library |

## Files

| File | Purpose |
|---|---|
| `pipelines/ingest.py` | `upsert_from_item()`, `queue_assets_for_episode()`, alias + re-air handling |
| `pipelines/checks.py` | `plan_downloads()`, `mark_asset_complete()`, `ensure_assets_for_episode()`, approval logic |
| `pipelines/postprocess.py` | `tag_audio()`, `move_to_library()`, `postprocess_episode()`, `rename_audio()` |
| `pipelines/library.py` | `build_paths_for_episode()`, `work_dir()`, `episode_file()` |
| `pipelines/gaps.py` | `gap_report()` — catalog vs downloaded comparison |
| `pipelines/html_scraper.py` | `scrape_episode_html()`, `build_comment()` — parse saved HTML for extra metadata |
| `pipelines/exporters.py` | `export_abs_metadata()` — write `metadata.json` for ABS |
| `catalog.py` | `scrape_catalog()`, `upsert_catalog()` — Wikipedia + mluvenypanacek.cz scrapers |
| `abs_client.py` | `trigger_library_scan()`, `get_library_items()` — ABS API client |
| `mediainfo.py` | `read_media_info()`, `apply_media_info()`, `MediaInfo` frozen dataclass — mutagen-based quality field population |
| `audioloader.py` | Legacy `audioloader` entry point |
| `__init__.py` | Empty |

## Planned (phase N)

- **Phase 2:** Inbox view — per-episode approval UI; currently only the API endpoint exists.
- **Phase 4:** Unsorted-folder scanner: walk legacy library and inbox folders, match files to DB works/episodes, three-bucket review (matched / duplicate / unknown).
- **Phase 4:** DB ↔ ID3 sync scan with field-by-field provenance diff.
- **Phase 5:** Completeness tracking with `WANTED` records; cross-source gap hunting.
- **Phase 6:** Full absorption of `scripts/abs_*.py` standalone scripts; ABS push triggered automatically after every successful postprocess.
