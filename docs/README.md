# Audiobiblio Documentation

Semi-automated audiobook management: monitor sources → review/approve → download → tag → dedupe → organize library → fill gaps. Runs on the Synology NAS; ABS is the player, audiobiblio is the brain.

## Where things are

- **Design spec (current redesign):** [superpowers/specs/2026-07-02-audiobiblio-redesign-design.md](superpowers/specs/2026-07-02-audiobiblio-redesign-design.md)
- **[workflows.md](workflows.md)** — the six core workflows, kept current; each step marked `[works today]` / `[partial]` / `[phase N]`
- **modules/** — one page per module: purpose, CLI usage, public API, standalone use
  - [core.md](modules/core.md) — config, DB models, provenance, URL normalization
  - [sources.md](modules/sources.md) — episode discovery plugins (mujrozhlas four-layer)
  - [acquire.md](modules/acquire.md) — crawl scheduling, download queue, yt-dlp execution
  - [tags.md](modules/tags.md) — tag read/write, genre taxonomy, naming, role fixes
  - [dedupe.md](modules/dedupe.md) — content-aware duplicate detection
  - [library.md](modules/library.md) — post-download pipeline, path builder, ABS integration
  - [web.md](modules/web.md) — FastAPI dashboard, REST API, SSE live updates
  - [tags/NAMING_CONVENTION.md](modules/tags/NAMING_CONVENTION.md) — folder/file naming scheme
  - [tags/GENRE_TAXONOMY_README.md](modules/tags/GENRE_TAXONOMY_README.md) — Czech genre taxonomy
  - [tags/TAG_ROLE_FIXES.md](modules/tags/TAG_ROLE_FIXES.md) — artist/performer role correction rules
- **decisions/** — dated architecture decision records: "chose X over Y because Z"
  - [0001-modular-monolith.md](decisions/0001-modular-monolith.md)
  - [0002-db-source-of-truth.md](decisions/0002-db-source-of-truth.md)
  - [phase2-import-linter-ignored-violations.md](decisions/phase2-import-linter-ignored-violations.md)
- **dead-ends/** — the anti-library: "tried X, failed because Y, don't retry unless Z changes"
  - [0001-audioloader-v003.md](dead-ends/0001-audioloader-v003.md)
  - [0002-download-sites-scripts.md](dead-ends/0002-download-sites-scripts.md)
  - [0003-metadata-fetch.md](dead-ends/0003-metadata-fetch.md)
  - [0004-exiftool-tag-analysis.md](dead-ends/0004-exiftool-tag-analysis.md)
  - [0005-radio-series-standalone-manager.md](dead-ends/0005-radio-series-standalone-manager.md)
  - [0006-stale-tests-importing-removed-symbols.md](dead-ends/0006-stale-tests-importing-removed-symbols.md)
  - [0007-channel-level-urls.md](dead-ends/0007-channel-level-urls.md)

## Ground rules

1. The metadata **database is the source of truth**; ID3 tags are a synced projection with field-level provenance.
2. Manual edits always outrank automatic values.
3. Directory names on disk are never modified without explicit approval.
4. A change that alters a module's behavior isn't done until its docs page is updated.
5. Failed experiments get a dead-ends record before the code is deleted.
6. Every phase merge updates `CHANGELOG.md` (added/fixed/findings/deferred), bumps the version in `pyproject.toml`, and snapshots the build journal into `docs/journal/`.
