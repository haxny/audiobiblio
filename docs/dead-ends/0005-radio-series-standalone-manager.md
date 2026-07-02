# 0005 — Standalone radio-series episode manager (radio_series.py)

**What we tried**

`archive/radio_series.py` (518 lines, Dec 2025 era) implemented a self-contained
`EpisodeDatabase` class backed by a JSON file on disk. It stored episode
metadata (number, title, date, description) for radio documentary series like
"Stopy, fakta, tajemství", provided fuzzy title-matching via normalized keys,
and could scrape episodes directly from mluvenypanacek.cz. A companion
`RadioSeriesOrganizer` class parsed SFT-prefixed filenames via `parse_filename()`
and `_split_title_subtitle()` methods; the filename formatting to
`SFT YYYYMMDD [NNN] Title.ext` was handled by `Episode.format_filename()`.
Helper scripts in `archive/scripts/` (`build_sft_database.py`, `scrape_sft_episodes.py`)
used it.

**Why it failed / why it was superseded**

The JSON-file database duplicated storage that SQLite (`core.db`) already
provides. The standalone manager had no connection to the provenance layer, so
enriched episode data could not be merged with metadata fetched from other
sources. The `EpisodeDatabase` write path was not concurrency-safe (plain
`json.dump` on the whole file). As of the Phase 1 redesign, `core.db.models`
has an `Episode` model with `EpisodeAlias` for title disambiguation, and
`reconcile.py` handles the SFT filename-parsing pattern. The scraping logic
in `scrape_sft_episodes.py` was a proof-of-concept only (generic HTML
pattern-matching, no site-specific selectors).

**Don't retry unless**

The radio-series workflow needs to be driven entirely offline without the SQLite
DB — e.g., on a device where the app cannot run. Even then, export the episodes
to a portable format from the DB rather than maintaining a parallel store.

**Where the code was**

`archive/radio_series.py`, `archive/scripts/build_sft_database.py`,
`archive/scripts/scrape_sft_episodes.py`, `archive/scripts/metadata_from_text.py`
(deleted with `archive/` in commit
`docs: mine archive/ into dead-ends + decisions, delete archive`).
