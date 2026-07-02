# tags — Audiobook tag management, genre taxonomy, and naming conventions

**Layer:** Layer 4 of 5 (same tier as `sources` and `dedupe`). May import from `core` only. Used by `library.pipelines.postprocess` and `acquire.downloader`.
**Standalone use:** `uv run tag-fixer` (entry point in `pyproject.toml`). Individual functions are importable as a library without a database or web server.

## Responsibilities

- Reads and writes ID3/MP4/Ogg metadata tags across all common audio formats (via mutagen).
- Normalises genre strings against a curated Czech-language genre taxonomy (`genre_taxonomy.json`); expands partial genre codes to their full semicolon-separated form.
- Strips diacritical marks from tag values using `unidecode` for maximum cross-device compatibility (Audiobookshelf, Plexamp, etc.).
- Applies role-assignment rules: corrects artist/albumartist/performer mismatches.
- Generates naming suggestions for existing folders of audio files (album-level and track-level).
- Writes `.nfo` XML sidecars for completed works.

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `write_tags` | `(path, album_tags, track_tags, cover_path=None)` | Write tag dict to any supported audio file |
| `read_tags` | `(path) -> dict` | Read all tags from an audio file |
| `find_audio_files` | `(folder) -> list[Path]` | Enumerate audio files in a folder |
| `aggregate_album_tags` | `(files) -> dict` | Majority-vote album-level tags across a file set |
| `fix_role_assignment` | `(tags) -> dict` | Correct artist/albumartist/performer roles |
| `suggest_album_tags` | `(folder_name, existing_tags, filenames) -> dict` | Propose album-level tag changes |
| `suggest_track_tags` | `(filename, existing_tags, album, author, …) -> dict` | Propose track-level tag changes |
| `strip_author_from_title` | `(title, author) -> str` | Remove redundant author prefix from a title string |
| `fix_track_title_redundancy` | `(tags) -> dict` | Remove album-name repetition from track titles |
| `detect_collection` | `(tags) -> bool` | Heuristic: is this a single-work or a collection? |
| `process_genre` | `(existing_genre, is_english=False) -> str` | Expand/normalise genre string via taxonomy |
| `strip_diacritics` | `(text) -> str` | Remove diacritics (unidecode wrapper) |
| `generate_suggestions` | `(folder) -> dict` | Full CLI-style analysis: album + per-track suggestions |

## Reference docs

The three reference documents cover the naming convention, genre taxonomy, and role-fix rules in detail. This page does not duplicate their content:

- [NAMING_CONVENTION.md](tags/NAMING_CONVENTION.md) — folder/file naming scheme, author/year/episode formatting
- [GENRE_TAXONOMY_README.md](tags/GENRE_TAXONOMY_README.md) — Czech genre taxonomy structure, codes, and expansion logic
- [TAG_ROLE_FIXES.md](tags/TAG_ROLE_FIXES.md) — when artist/albumartist/performer are wrong and how to fix them

## Files

| File | Purpose |
|---|---|
| `writer.py` | `write_tags()` — mutagen-backed writer for M4A, MP3, Ogg, FLAC |
| `reader.py` | `read_tags()`, `find_audio_files()`, `aggregate_album_tags()` |
| `rules.py` | Suggestion and role-fix logic |
| `genre.py` | `process_genre()` + JSON taxonomy loader |
| `diacritics.py` | `strip_diacritics()` wrapper |
| `naming.py` | Filename and folder-name construction utilities |
| `nfo.py` | `write_nfo()` — write Kodi-compatible `.nfo` XML sidecar |
| `cli.py` | `generate_suggestions(folder)` — CLI orchestration layer |
| `__init__.py` | Public re-exports (all names in `__all__`) |

## Planned (phase N)

- **Phase 3:** Tag carry-over on upgrade: curated tags from the old file are preserved when a better-quality version replaces it.
- **Phase 4:** Web tag-fixer UI (current vs proposed side-by-side, apply from browser).
- **Phase 5:** Enrichment integration: `MetadataValue` rows (with `ENRICHED` provenance) written to tags via `write_tags()`.
