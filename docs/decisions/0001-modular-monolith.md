# 0001 — Modular monolith over multi-package workspace or SPA rewrite

**Date:** 2026-07-02 · **Status:** accepted

Choice A (evolve this repo, strict module boundaries, one container) was chosen
over B (fresh multi-package workspace, port module by module) and C (React/Vue
SPA over existing backend).

Criteria: time-to-daily-use, restart risk (the archive/ graveyard shows prior
iterations died in rewrites), one-person maintenance, real-data testing.
Modularity is enforced by import-linter layers (pyproject.toml), not by
packaging. Modules stay extractable later.

Full analysis: docs/superpowers/specs/2026-07-02-audiobiblio-redesign-design.md
