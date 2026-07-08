# sources — Episode discovery plugins for each source site

**Layer:** Layer 4 of 5 (same tier as `dedupe` and `tags`). May import from `core` only. No module at the same layer imports from `sources` except `acquire` (one tier up).
**Standalone use:** Library-only today; no dedicated top-level CLI commands. Called by `acquire.crawler` and the `ingest-program`, `ingest-url`, and `crawl-url` CLI commands.

## Responsibilities

- Probes a URL with yt-dlp and classifies it as `episode`, `series`, or `program` (`mrz_inspector`).
- Discovers all episodes under a mujrozhlas.cz program URL using four complementary layers: yt-dlp flat-playlist, AJAX pagination, HTML scraping, and the RAPI JSON API (`discovery`).
- Merges results from all four layers into a single deduplicated `DiscoveredEpisode` list with source attribution (`{"ytdlp", "ajax", "html", "rapi"}`).
- Normalizes rozhlas.cz subdomain URLs to their mujrozhlas.cz equivalents before probing.
- Handles the yt-dlp "shared program URL" bug where all entries in a flat-playlist resolve to the container URL: cross-matches by ext_id and title slug to assign episode-level URLs from AJAX/HTML.

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `discover_program` | `(url, *, skip_ajax=False, skip_html=False, skip_rapi=False) -> list[DiscoveredEpisode]` | Multi-source discovery for a program URL |
| `DiscoveredEpisode` | dataclass | `url, title, ext_id, duration_s, description, published_at, series, author, uploader, is_series_episode, sources, original` |
| `probe_url` | `(url) -> dict` | Run `yt-dlp --flat-playlist -J` and return parsed JSON |
| `classify_probe` | `(data, url) -> ProbeResult` | Classify yt-dlp output into episode/series/program with entries |
| `mrz_discover_children` | `(url) -> list[tuple[str, str]]` | HTML scrape of a mujrozhlas.cz page; return `[(url, title)]` |
| `mrz_discover_children_depth` | `(url, want_depth) -> list[tuple[str, str]]` | Discover children at a specific URL depth level |
| `deep_probe_kind` | `(url) -> str` | Full yt-dlp probe to classify URL as `episode`, `series`, or `program` |
| `normalize_rozhlas_url` | `(url) -> str` | Convert `plus.rozhlas.cz/show-9391766` → `www.mujrozhlas.cz/show` |

## Files

| File | Purpose |
|---|---|
| `mrz_inspector.py` | yt-dlp probe + HTML scraper + URL depth classifier |
| `discovery.py` | Four-layer discovery + merge: `discover_program()` |
| `rapi.py` | `api.mujrozhlas.cz/shows/{uuid}/episodes` JSON client |
| `databazeknih.py` | `www.databazeknih.cz` book-metadata client + enrichment |
| `__init__.py` | Empty (no public re-exports at package level) |

## databazeknih enrichment (Phase 5 Task 6)

`databazeknih.py` provides on-demand enrichment for `Work` entities.

| Name | Signature | Purpose |
|---|---|---|
| `search_book` | `(title, author=None) -> list[DbkHit]` | Search `/search?q=...&in=books`; parse `<a class="new" type="book">` hits |
| `fetch_book` | `(url) -> DbkBook \| None` | Fetch and parse a `/prehled-knihy/SLUG` page |
| `enrich_work_from_dbk` | `(session, work) -> EnrichReport` | Best-hit fuzzy match (SequenceMatcher > 0.85) → fetch → record ENRICHED provenance |
| `DbkHit` | frozen dataclass | `url, title, author` |
| `DbkBook` | dataclass | `title, author, year, description, genres, narrator, cover_url` |
| `EnrichReport` | dataclass | `skipped, reason, fields_set, source_url` |

**Rate limiter:** module-level `_dbk_limiter = RateLimiter(rate=0.5, burst=1)` — 1 req / 2 s.
**UA:** `"audiobiblio/0.5 (personal audiobook manager)"` — never raises on HTTP/parse errors (logs warning, returns `[]/None`).

**Routing:**
- `year` → work-level ORM; set only when `work.year is None` and no `MANUAL` provenance row exists; always records `ENRICHED` row.
- `description` → provenance-only on `entity_type="work"` (Work has no `description` column); sync/display layers project from MetadataValue.
- `genre` → episode-level (`genre` absent from `WORK_FIELDS` by design); ENRICHED row per episode, comma-joined genre string.
- `narrator` → episode-level; ENRICHED row per episode when non-None.
- Cache: raw result stored in `work.extra["dbk"]` via dict reassignment.

**API:** `POST /api/v1/works/{id}/enrich` → submits background task via `task_tracker`; returns `{"task_id": "..."}` immediately. 404 for unknown work.
**UI:** Episode detail page has "Re-enrich z databazeknih" button in the Metadata & provenance card; uses `apiJson('POST', ...)` (fire-and-forget + reload).

## Planned (phase N)

- **Phase 2:** Per-source plugin contract (`list_items(url)`, `fetch_metadata(url)`) to make it straightforward to add new sources (sktorrent, cdwifi, manual-URL) without changes to `acquire`.
- **Phase 2:** Source health tracking — repeated failures flag the source in the Console.
- **Phase 5:** Cross-source episode matching (gap hunting): a newly discovered episode on any source is fuzzy-matched against the wanted list.
- **Future:** Plugins for additional sources beyond the current five (mujrozhlas, rozhlas, sktorrent, cdwifi, manual-URL).
