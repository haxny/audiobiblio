# Global Search — Implementation Report

**Branch:** `feature/global-search` (off main @ 98df0e8)
**Commit:** `da5d6a9` — `feat: global search across works, episodes and programs`
**Tests:** 607 passed, 2 skipped (was 594+2; +13 new)

## What was built

`GET /search?q=...` — global search across three entities, plus a compact
search box on the right side of the header nav on every page.

| Section  | Fields searched            | Row links to                              |
|----------|----------------------------|-------------------------------------------|
| Díla     | `Work.title`, `Work.author`| first episode's detail (`/episodes/{id}`) |
| Epizody  | `Episode.title`, `Episode.summary` | `/episodes/{id}`                  |
| Pořady   | `Program.name`             | `/episodes?q={name}` (list search prefilled — same `q` param episodes.html uses) |

Each section shows an uncapped total count and is capped at 50 rows
(`_SEARCH_LIMIT`); the header shows "(N, zobrazeno 50)" when truncated.
Empty/whitespace `q` renders an empty-state card.

## Files changed

- `audiobiblio/web/views.py` — `_query_search()` helper (pure-ish, same
  testable pattern as `_query_gaps`), `_search_norm()`, `_EMPTY_SEARCH`,
  `_SEARCH_LIMIT`, and the `GET /search` route. First-episode IDs are
  batched with `func.min(Episode.id) GROUP BY work_id` — same pattern as
  the gaps view. Work titles for shown episodes are batch-fetched too.
- `audiobiblio/web/templates/search.html` — new dense results page with
  three cards (Díla / Epizody / Pořady) using the existing `.card` /
  `.dense-table` / `.btn-sm btn-outline` styles.
- `audiobiblio/web/templates/base.html` — compact `<form action="/search">`
  with a `q` search input, placed after `<nav>` inside `header .inner`
  (flex layout places it on the right; wraps on narrow screens).
- `audiobiblio/web/static/audiobiblio.css` — 5 rules for `.nav-search`
  (translucent input matching the navy header, white on focus).
- `tests/web/test_search_view.py` — 13 tests (TDD; written first, verified
  RED, then implemented to GREEN).

## Design decision: diacritics matching in Python, not SQL LIKE

The spec asked for LIKE queries plus unidecode. LIKE alone cannot satisfy
the required test "search `hasek` finds `Hašek`" — the diacritics live in
the *stored* value, and SQLite has no unaccent function to strip them
DB-side (registering a custom SQLite function per-connection was rejected
as fragile and driver-specific). So `_query_search` scans narrow column
tuples (`id` + text columns only) and matches `unidecode(value).lower()`
against `unidecode(q).lower()` in Python. This is symmetric (query with or
without diacritics both work), correct, and fine at this project's scale
(personal library, SQLite). If the library grows, a stored normalized
column with an index would be the upgrade path.

## Test coverage (tests/web/test_search_view.py)

Helper (`_query_search`, direct-call pattern like `test_gaps_view.py`):
- empty q → empty sections + zero totals
- work title hit, work author hit
- work row carries `first_episode_id`
- episode title hit, episode summary hit
- program name hit
- diacritics-insensitive both directions ("hasek"→"Hašek", "žert"→"Zert")
- case-insensitive
- cap at 50 with uncapped total (60 episodes → 50 shown, total 60)

Route (minimal app with views router, pattern from `test_episode_detail.py`):
- `GET /search?q=hasek` renders matching work (200)
- `GET /search` with no q renders empty state (200)

## Verification

- `uv run pytest` → 607 passed, 2 skipped
- No server was started (port 8080 untouched; live check not needed —
  route covered by TestClient tests).
- main branch untouched; work done solely on `feature/global-search`.
