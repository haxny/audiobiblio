# core — Configuration, database models, and shared utilities

**Layer:** Bottom (layer 5 of 5). Depends on nothing else in the project. Every other module may import from `core`; `core` never imports from them.
**Standalone use:** `uv run audiobiblio init` (create/migrate DB), `uv run audiobiblio paths` (show storage locations). Both commands work without the web server.

## Responsibilities

- Loads `config.yaml` with environment variable overrides and exposes a single `Config` dataclass.
- Owns the SQLAlchemy ORM: all tables live here (`Station`, `Program`, `Series`, `Work`, `Episode`, `Asset`, `DownloadJob`, `CrawlTarget`, `MetadataValue`, and domain-specific tables for CD WiFi and torrents).
- Provides the session factory (`get_session()`, `init_db()`) used by every other module.
- Implements provenance resolution: `MANUAL > ENRICHED > FILE > SCRAPED`, with tie-breaking by `observed_at`.
- Exposes canonical URL normalization helpers (`norm_url`, `norm_url_strip_reair`) used by `dedupe` and `library`.
- Manages structured logging setup (structlog) and the shared rate limiter for mujrozhlas.cz (0.5 rps).

## Public interface

| Name | Signature | Purpose |
|---|---|---|
| `load_config` | `(config_path=None) -> Config` | Load config.yaml + env vars into a `Config` dataclass |
| `Config` | dataclass | All tunable settings (db_url, library_dir, abs_url, intervals, …) |
| `init_db` | `(db_url=None)` | Create tables + run Alembic migrations |
| `get_session` | `() -> Session` | Return the shared SQLAlchemy session |
| `resolve_field` | `(candidates: Sequence[MetadataValue]) -> MetadataValue | None` | Return winning value by provenance rank and recency |
| `norm_url` | `(u: str | None) -> str` | Lowercase host, strip trailing slash |
| `norm_url_strip_reair` | `(u: str | None) -> str` | `norm_url` + strip re-air numeric suffix (`-2941669`) |
| `setup_logging` | `()` | Configure structlog for the process |
| `mrz_limiter` | rate limiter instance | Call `.wait()` before each mujrozhlas.cz HTTP request |

Key ORM models (all in `audiobiblio.core.db.models`):

| Model | Purpose |
|---|---|
| `Station` | Radio station (code + website) |
| `Program` | Show within a station; holds genre, crawl schedule |
| `Series` | Named grouping inside a program |
| `Work` | Concrete book/album (author, year, ASIN) |
| `Episode` | Single downloadable episode; has availability tracking |
| `EpisodeAlias` | Alternate URLs for re-aired episodes |
| `Asset` | One file per type (AUDIO, META_JSON, WEBPAGE, COVER) |
| `DownloadJob` | Queue row: PENDING / APPROVAL / RUNNING / SUCCESS / ERROR / WATCH |
| `CrawlTarget` | URL + schedule for periodic discovery |
| `MetadataValue` | Field-level provenance store (entity_type, field, value, origin, source) |
| `CdwifiDownload` | CD WiFi train-portal download log |
| `TorrentEntry` | sktorrent.eu scraped catalog |
| `CatalogEntry` | Reference episode list (Wikipedia, mluvenypanacek.cz) |
| `AvailabilityLog` | Per-check HTTP probe results for an episode |

## Files

| File | Purpose |
|---|---|
| `config.py` | `Config` dataclass + `load_config()` |
| `db/models.py` | All SQLAlchemy ORM models and enums |
| `db/session.py` | `init_db()`, `get_session()` |
| `logging_setup.py` | structlog initialization |
| `provenance.py` | `resolve_field()` and `_ORIGIN_RANK` |
| `ratelimit.py` | `mrz_limiter` token-bucket rate limiter |
| `urls.py` | `norm_url()`, `norm_url_strip_reair()` |

## Planned (phase N)

- **Phase 2:** Alembic migration for any new fields added during daily-loop work.
- **Phase 2:** `core.ports` — typed callback/event interfaces so `acquire` can decouple from `library` (resolves the parked import-linter violations; see [decisions/phase2-import-linter-ignored-violations.md](../decisions/phase2-import-linter-ignored-violations.md)).
- **Phase 5:** `MetadataValue` rows populated from enrichment sources (databazeknih); provenance resolution wired into tag writes.
