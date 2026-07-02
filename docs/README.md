# Audiobiblio Documentation

Semi-automated audiobook management: monitor sources → review/approve → download → tag → dedupe → organize library → fill gaps. Runs on the Synology NAS; ABS is the player, audiobiblio is the brain.

## Where things are

- **Design spec (current redesign):** [superpowers/specs/2026-07-02-audiobiblio-redesign-design.md](superpowers/specs/2026-07-02-audiobiblio-redesign-design.md)
- **modules/** — one page per module: purpose, CLI usage, public API, standalone use *(populated during Phase 1)*
- **decisions/** — dated architecture decision records: "chose X over Y because Z"
- **dead-ends/** — the anti-library: "tried X, failed because Y, don't retry unless Z changes" *(mined from archive/ during Phase 1)*
- **workflows.md** — the core workflows, kept current *(created during Phase 1)*

## Ground rules

1. The metadata **database is the source of truth**; ID3 tags are a synced projection with field-level provenance.
2. Manual edits always outrank automatic values.
3. Directory names on disk are never modified without explicit approval.
4. A change that alters a module's behavior isn't done until its docs page is updated.
5. Failed experiments get a dead-ends record before the code is deleted.
