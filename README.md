# audiobiblio

Semi-automated audiobook management for mujrozhlas.cz (Czech Radio). Monitors sources, queues downloads for review, downloads via yt-dlp, writes metadata tags, deduplicates, and organizes a library. Runs on Synology NAS; Audiobookshelf is the player.

## Module layout

```
audiobiblio/
├── core/       config, DB session/models, provenance, URL normalization. Depends on nothing.
├── sources/    episode discovery: yt-dlp + AJAX + HTML + RAPI (four-layer merge).
├── acquire/    CrawlTargets + APScheduler, download queue, yt-dlp/JDownloader execution.
├── tags/       tag read/write, genre taxonomy, diacritics, naming, role fixes, NFO.
├── dedupe/     duplicate matching: ext_id → URL → fuzzy title; quality scoring (phase 3).
├── library/    post-download pipeline, canonical paths, ABS client, gap reports.
└── web/        FastAPI dashboard + REST API + SSE. Depends on all; nothing depends on it.
```

Import-linter enforces this order. Four violations are parked as phase 2 backlog (see [docs/decisions/phase2-import-linter-ignored-violations.md](docs/decisions/phase2-import-linter-ignored-violations.md)).

## Dev install

```bash
uv sync
```

## Run

```bash
# Initialize the database
uv run audiobiblio init

# Show storage locations
uv run audiobiblio paths

# Start the web dashboard + scheduler
uv run audiobiblio serve

# One-shot: ingest all episodes from a program URL
uv run audiobiblio ingest-program --url https://www.mujrozhlas.cz/program-name

# Execute pending download jobs
uv run audiobiblio run-jobs

# Add a crawl target (auto-monitored)
uv run audiobiblio target-add --url https://www.mujrozhlas.cz/program --kind program

# See all commands
uv run audiobiblio --help
```

## Test

```bash
uv run pytest
```

## Documentation

See [docs/README.md](docs/README.md) for the full documentation index:

- Module pages (purpose, CLI, public API, standalone use)
- Workflows — six core workflows with `[works today]` / `[phase N]` markers
- Architecture decision records
- Dead-ends (anti-library)

## Configuration

Copy `config.yaml.example` to `config.yaml` and edit, or use environment variables:

| Variable | Purpose |
|---|---|
| `AUDIOBIBLIO_DB_URL` | SQLAlchemy DB URL (default: local SQLite) |
| `AUDIOBIBLIO_LIBRARY_DIR` | Root path for downloaded files |
| `ABS_URL` | Audiobookshelf base URL |
| `ABS_API_KEY` | Audiobookshelf API token |
| `JD_HOST` / `JD_PORT` | JDownloader MyJDownloader host/port |
