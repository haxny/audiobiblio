# Phase 1: Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure audiobiblio into strictly-bounded modules (core/sources/acquire/tags/dedupe/library/web), protect the move with characterization tests, add DB provenance groundwork, and mine `archive/` into documentation before deleting it.

**Architecture:** Modular monolith evolved in place (spec: `docs/superpowers/specs/2026-07-02-audiobiblio-redesign-design.md`). Existing code moves into module packages via `git mv` + import rewrites; behavior does not change in this phase except one consolidation (`_norm_url`). Tests come FIRST so the restructure is verifiable.

**Tech Stack:** Python ≥3.10, SQLAlchemy 2.0, Alembic, pytest, import-linter, uv (uv.lock present — use `uv run` for everything).

## Global Constraints

- DB schema is extended, never restarted; new Alembic migration must chain from head `584f34ff6085`.
- No behavior changes during file moves — `git mv` + import updates only.
- Directory names on disk (user's media) are never touched by any code in this phase.
- All commits follow `<type>: <description>` (feat/fix/refactor/docs/test/chore). No AI attribution lines.
- Run tests with `uv run pytest -q`. Never `pip install` — dependencies are added to `pyproject.toml` and `uv lock` regenerates the lockfile.
- Python: PEP 8, type annotations on all new function signatures, structlog for logging (no `print` in library code).
- Working dir: `/Users/jirislovacek/projects/audiobiblio`, branch `main`.

---

### Task 1: Test infrastructure + diacritics characterization tests

The codebase has **zero tests**. Before moving anything, pin down the pure-logic behavior we must not break. These are characterization tests of existing code — they should pass immediately; a failure means you discovered a real bug (stop and report it, don't "fix" the test).

**Files:**
- Modify: `pyproject.toml` (add dev dependency group)
- Create: `tests/__init__.py` (empty)
- Create: `tests/tags/__init__.py` (empty)
- Test: `tests/tags/test_diacritics.py`

**Interfaces:**
- Consumes: `audiobiblio.tags.diacritics` — `strip_diacritics(text: str) -> str`, `fix_windows1250(text: str) -> str`, `detect_czech_content(folder_name: str, filenames: list[str]) -> bool`, `apply_czech_parts_replacement(text: str) -> str`
- Produces: a working `uv run pytest` toolchain all later tasks rely on.

- [ ] **Step 1: Add pytest to dev dependencies**

Append to `pyproject.toml` (after the `[project.optional-dependencies]` section):

```toml
[dependency-groups]
dev = [
  "pytest>=8.0",
  "import-linter>=2.0",
]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: resolves and installs pytest + import-linter, updates `uv.lock`.

- [ ] **Step 3: Write the characterization tests**

Create `tests/__init__.py` and `tests/tags/__init__.py` (both empty), then `tests/tags/test_diacritics.py`:

```python
"""Characterization tests for audiobiblio.tags.diacritics.

These pin down EXISTING behavior before the module restructure.
If one fails, the code has a real bug — report it, do not adjust the test.
"""
from audiobiblio.tags.diacritics import (
    apply_czech_parts_replacement,
    detect_czech_content,
    fix_windows1250,
    strip_diacritics,
)


class TestStripDiacritics:
    def test_czech_lowercase(self):
        assert strip_diacritics("příliš žluťoučký kůň") == "prilis zlutoucky kun"

    def test_czech_uppercase(self):
        assert strip_diacritics("ŘEŘICHA ŽÍŽALA") == "RERICHA ZIZALA"

    def test_ascii_passthrough(self):
        assert strip_diacritics("Karel Capek") == "Karel Capek"

    def test_empty_string(self):
        assert strip_diacritics("") == ""

    def test_win1250_corruption_also_stripped(self):
        # 'ø' is a corrupted 'ř' in Win-1250-as-Latin-1 tags
        assert strip_diacritics("Døevo") == "Drevo"


class TestFixWindows1250:
    def test_clean_text_unchanged(self):
        assert fix_windows1250("Bílá nemoc") == "Bílá nemoc"

    def test_empty_unchanged(self):
        assert fix_windows1250("") == ""

    def test_marker_triggers_recode(self):
        # Text containing a Win-1250 marker gets re-decoded; result must
        # differ from input and not raise.
        corrupted = "høbitov"
        fixed = fix_windows1250(corrupted)
        assert fixed != corrupted


class TestDetectCzechContent:
    def test_czech_chars_in_folder(self):
        assert detect_czech_content("Povídky Čapek", []) is True

    def test_czech_chars_in_filename(self):
        assert detect_czech_content("Books", ["01 příběh.mp3"]) is True

    def test_czech_word_in_folder(self):
        assert detect_czech_content("Sedm povidka kapitola", []) is True

    def test_english_content(self):
        assert detect_czech_content("The Hobbit", ["01 Chapter One.mp3"]) is False


class TestCzechPartsReplacement:
    def test_cast_prvni(self):
        assert apply_czech_parts_replacement("Osada, cast prvni") == "Osada-01"

    def test_no_match_unchanged(self):
        assert apply_czech_parts_replacement("Osada") == "Osada"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tags/test_diacritics.py -v`
Expected: all PASS. If any FAIL, stop — report the discrepancy to the user before continuing.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/
git commit -m "test: pytest infrastructure + diacritics characterization tests"
```

---

### Task 2: Naming characterization tests

**Files:**
- Test: `tests/tags/test_naming.py`

**Interfaces:**
- Consumes: `audiobiblio.tags.naming` — `sanitize_filename(text: str) -> str`, `generate_filename(tags: dict, track_index: int, total_tracks: int, extension: str) -> str`, `generate_folder_name(album_tags: dict) -> str`

- [ ] **Step 1: Write the characterization tests**

Create `tests/tags/test_naming.py`:

```python
"""Characterization tests for audiobiblio.tags.naming (NAMING_CONVENTION.md patterns)."""
from audiobiblio.tags.naming import (
    generate_filename,
    generate_folder_name,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_strips_diacritics(self):
        assert sanitize_filename("Žluťoučký") == "Zlutoucky"

    def test_removes_forbidden_chars(self):
        assert sanitize_filename('A/B\\C:D*E?F"G<H>I|J') == "A-B-CDEFGHIJ"

    def test_collapses_whitespace(self):
        assert sanitize_filename("  Karel   Capek  ") == "Karel Capek"

    def test_empty(self):
        assert sanitize_filename("") == ""


class TestGenerateFilename:
    BASE_TAGS = {
        "albumartist": "Ota Pavel",
        "album": "Sedm deka zlata",
        "date": "1980",
    }

    def test_single_file_with_year(self):
        # Pattern 1: {albumartist} - ({date}) {album}.ext
        name = generate_filename(dict(self.BASE_TAGS), 1, 1, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata.mp3"

    def test_single_file_no_year(self):
        tags = dict(self.BASE_TAGS)
        tags["date"] = ""
        name = generate_filename(tags, 1, 1, ".mp3")
        assert name == "Ota Pavel - Sedm deka zlata.mp3"

    def test_multitrack_no_title(self):
        # Pattern 2: ... - {track}.ext, zero-padded
        name = generate_filename(dict(self.BASE_TAGS), 3, 10, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata - 03.mp3"

    def test_multitrack_with_title(self):
        # Pattern 3: ... - {track} {title}.ext
        tags = dict(self.BASE_TAGS, title="Zlate uhori")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert name == "Ota Pavel - (1980) Sedm deka zlata - 01 Zlate uhori.mp3"

    def test_tracknumber_tag_overrides_index(self):
        tags = dict(self.BASE_TAGS, tracknumber="7")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert " - 07.mp3" in name

    def test_tracknumber_with_total_uses_number_part(self):
        # "7/12" must yield 07, not crash (plain numbers rule)
        tags = dict(self.BASE_TAGS, tracknumber="7/12")
        name = generate_filename(tags, 1, 10, ".mp3")
        assert " - 07.mp3" in name

    def test_disc_number_prefixes_track(self):
        # Pattern 6: disc 2 track 3 -> 203
        tags = dict(self.BASE_TAGS, discnumber="2", title="Kapitola")
        name = generate_filename(tags, 3, 20, ".mp3")
        assert " - 203 Kapitola.mp3" in name

    def test_long_title_truncated_to_filesystem_limit(self):
        tags = dict(self.BASE_TAGS, title="x" * 300)
        name = generate_filename(tags, 1, 10, ".mp3")
        assert len(name) <= 250


class TestGenerateFolderName:
    def test_with_year(self):
        tags = {"albumartist": "Ota Pavel", "album": "Sedm deka zlata", "date": "1980-05-01"}
        assert generate_folder_name(tags) == "Ota Pavel - (1980) Sedm deka zlata"

    def test_without_year(self):
        tags = {"albumartist": "Ota Pavel", "album": "Sedm deka zlata"}
        assert generate_folder_name(tags) == "Ota Pavel - Sedm deka zlata"

    def test_falls_back_to_artist(self):
        tags = {"artist": "Ota Pavel", "album": "Sedm deka zlata"}
        assert generate_folder_name(tags) == "Ota Pavel - Sedm deka zlata"
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/tags/test_naming.py -v`
Expected: all PASS (characterization). On FAIL: stop, report.

- [ ] **Step 3: Commit**

```bash
git add tests/tags/test_naming.py
git commit -m "test: naming convention characterization tests"
```

---

### Task 3: Dedupe characterization tests

**Files:**
- Create: `tests/dedupe/__init__.py` (empty)
- Test: `tests/dedupe/test_matching.py`

**Interfaces:**
- Consumes: `audiobiblio.dedupe` — `_norm_url(u: str | None) -> str`, `_norm_url_strip_reair(u: str | None) -> str`, `_norm_title(title: str | None, series_prefix: str | None = None) -> str`, `dedupe_discovered(entries: list, existing_episodes: list | None = None, series_prefix: str | None = None) -> tuple[list, list[DuplicateGroup]]`
- Produces: the test module that Task 5 re-points at the new package location.

- [ ] **Step 1: Write the characterization tests**

Create `tests/dedupe/__init__.py` (empty) and `tests/dedupe/test_matching.py`:

```python
"""Characterization tests for the 3-tier dedupe logic."""
from dataclasses import dataclass

from audiobiblio.dedupe import (
    _norm_title,
    _norm_url,
    _norm_url_strip_reair,
    dedupe_discovered,
)


@dataclass
class FakeEntry:
    url: str | None = None
    title: str | None = None
    ext_id: str | None = None


class TestNormUrl:
    def test_lowercases_host_strips_slash(self):
        assert _norm_url("https://MujRozhlas.CZ/podcast/") == "https://mujrozhlas.cz/podcast"

    def test_none_is_empty(self):
        assert _norm_url(None) == ""

    def test_strips_query_and_fragment(self):
        assert _norm_url("https://a.cz/x?p=1#f") == "https://a.cz/x"


class TestNormUrlStripReair:
    def test_strips_seven_digit_suffix(self):
        assert (
            _norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2941669")
            == "https://mujrozhlas.cz/hra/osada"
        )

    def test_keeps_short_numeric_suffix(self):
        # Short numbers are legitimate (e.g. "-2" part numbering), only 7+ digits are re-air IDs
        assert (
            _norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2")
            == "https://mujrozhlas.cz/hra/osada-2"
        )


class TestNormTitle:
    def test_strips_diacritics_and_lowercases(self):
        assert _norm_title("Bílá Nemoc") == "bila nemoc"

    def test_strips_series_prefix(self):
        assert _norm_title("Osada: dil prvni", series_prefix="Osada") == "dil prvni"

    def test_none_is_empty(self):
        assert _norm_title(None) == ""


class TestDedupeDiscovered:
    def test_tier1_ext_id_match(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Osada 1", ext_id="uuid-1"),
            FakeEntry(url="https://b.cz/other", title="Different", ext_id="uuid-1"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "ext_id"

    def test_tier2_reair_url_match(self):
        entries = [
            FakeEntry(url="https://a.cz/hra/osada-2941669", title="Osada"),
            FakeEntry(url="https://a.cz/hra/osada-3000001", title="totally different title"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "url_reair"

    def test_tier3_fuzzy_title_match(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Bila nemoc, cast prvni"),
            FakeEntry(url="https://b.cz/2", title="Bílá nemoc, část první"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 1
        assert groups[0].duplicates[0]["reason"] == "title_fuzzy"

    def test_generic_titles_never_fuzzy_matched(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Epizody pořadu"),
            FakeEntry(url="https://b.cz/2", title="Epizody pořadu"),
        ]
        unique, _groups = dedupe_discovered(entries)
        # Same generic title on different URLs must NOT collapse
        assert len(unique) == 2

    def test_existing_db_episode_blocks_reimport(self):
        entries = [FakeEntry(url="https://a.cz/hra/osada", title="Osada")]
        existing = [FakeEntry(url="https://a.cz/hra/osada", title="Osada", ext_id="uuid-9")]
        unique, groups = dedupe_discovered(entries, existing_episodes=existing)
        assert len(unique) == 0
        assert groups[0].canonical_url == "(existing in DB)"

    def test_distinct_entries_all_kept(self):
        entries = [
            FakeEntry(url="https://a.cz/1", title="Osada, cast prvni"),
            FakeEntry(url="https://a.cz/2", title="Zahrada, cast druha"),
        ]
        unique, groups = dedupe_discovered(entries)
        assert len(unique) == 2
        assert groups == []
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/dedupe/ -v`
Expected: all PASS. On FAIL: stop, report.

- [ ] **Step 3: Commit**

```bash
git add tests/dedupe/
git commit -m "test: dedupe 3-tier matching characterization tests"
```

---

### Task 4: `core.urls` — consolidate URL normalization (real TDD)

`_norm_url` is duplicated in `audiobiblio/dedupe.py:40` and `audiobiblio/crawler.py:21` (and `discovery.py` has a variant `_norm_url_for_merge`). One public home: `audiobiblio/core/urls.py`. This is the only *new* code before the restructure, so `core/` exists as a package before files move into it.

**Files:**
- Create: `audiobiblio/core/__init__.py` (empty)
- Create: `audiobiblio/core/urls.py`
- Modify: `audiobiblio/dedupe.py` (delete local `_norm_url`/`_norm_url_strip_reair`, import from core)
- Modify: `audiobiblio/crawler.py` (delete local `_norm_url`, import from core)
- Test: `tests/core/__init__.py`, `tests/core/test_urls.py`

**Interfaces:**
- Produces: `audiobiblio.core.urls.norm_url(u: str | None) -> str` and `audiobiblio.core.urls.norm_url_strip_reair(u: str | None) -> str` — public (no underscore). All later modules use these; never re-implement URL normalization.

- [ ] **Step 1: Write the failing test**

Create `tests/core/__init__.py` (empty) and `tests/core/test_urls.py`:

```python
from audiobiblio.core.urls import norm_url, norm_url_strip_reair


def test_norm_url_lowercases_host_strips_slash():
    assert norm_url("https://MujRozhlas.CZ/podcast/") == "https://mujrozhlas.cz/podcast"


def test_norm_url_none():
    assert norm_url(None) == ""


def test_strip_reair_seven_digits():
    assert (
        norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2941669")
        == "https://mujrozhlas.cz/hra/osada"
    )


def test_strip_reair_keeps_short_suffix():
    assert (
        norm_url_strip_reair("https://mujrozhlas.cz/hra/osada-2")
        == "https://mujrozhlas.cz/hra/osada-2"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_urls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audiobiblio.core'`

- [ ] **Step 3: Implement `core/urls.py`**

Create `audiobiblio/core/__init__.py` (empty) and `audiobiblio/core/urls.py` — the bodies are moved verbatim from `dedupe.py` lines 22 and 40–63, renamed public:

```python
"""URL normalization — the single home for URL comparison logic.

Moved from dedupe.py/crawler.py duplicates (see docs/decisions/).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Trailing numeric suffix pattern (re-air IDs like -2941669)
_REAIR_SUFFIX_RE = re.compile(r"-\d{7,}$")


def norm_url(u: str | None) -> str:
    """Basic URL normalization: lowercase host, strip trailing slash."""
    if not u:
        return ""
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return u.strip().rstrip("/")


def norm_url_strip_reair(u: str | None) -> str:
    """Normalize URL and strip trailing re-air numeric suffixes."""
    norm = norm_url(u)
    if not norm:
        return ""
    try:
        p = urlparse(norm)
        path = _REAIR_SUFFIX_RE.sub("", p.path)
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))
    except Exception:
        return norm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_urls.py -v`
Expected: PASS

- [ ] **Step 5: Re-point dedupe.py and crawler.py to core**

In `audiobiblio/dedupe.py`: delete the `_REAIR_SUFFIX_RE` constant and the functions `_norm_url` (lines 40–50) and `_norm_url_strip_reair` (lines 53–63); add at the imports:

```python
from audiobiblio.core.urls import norm_url as _norm_url, norm_url_strip_reair as _norm_url_strip_reair
```

(The alias keeps every internal call site and the Task 3 tests unchanged.)

In `audiobiblio/crawler.py`: delete its local `_norm_url` (line 21 region) and add the same import line. Also delete `urlparse`/`urlunparse` imports if now unused in each file (`uv run python -c "import audiobiblio.dedupe, audiobiblio.crawler"` must stay clean).

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS (Tasks 1–4 tests).

- [ ] **Step 7: Commit**

```bash
git add audiobiblio/core/ audiobiblio/dedupe.py audiobiblio/crawler.py tests/core/
git commit -m "refactor: consolidate URL normalization into core.urls"
```

---

### Task 5: Module restructure — file moves + import rewrites

Pure mechanical move; **no behavior change**. Every move is `git mv` (preserves history) followed by a global import rewrite, verified by the test suite plus an import smoke check.

**Files (complete move map):**

| From | To |
|---|---|
| `audiobiblio/config.py` | `audiobiblio/core/config.py` |
| `audiobiblio/logging_setup.py` | `audiobiblio/core/logging_setup.py` |
| `audiobiblio/ratelimit.py` | `audiobiblio/core/ratelimit.py` |
| `audiobiblio/db/` (models.py, session.py) | `audiobiblio/core/db/` |
| `audiobiblio/discovery.py` | `audiobiblio/sources/discovery.py` |
| `audiobiblio/mrz_inspector.py` | `audiobiblio/sources/mrz_inspector.py` |
| `audiobiblio/rapi.py` | `audiobiblio/sources/rapi.py` |
| `audiobiblio/crawler.py` | `audiobiblio/acquire/crawler.py` |
| `audiobiblio/downloader.py` | `audiobiblio/acquire/downloader.py` |
| `audiobiblio/scheduler.py` | `audiobiblio/acquire/scheduler.py` |
| `audiobiblio/availability.py` | `audiobiblio/acquire/availability.py` |
| `audiobiblio/jdownloader.py` | `audiobiblio/acquire/jdownloader.py` |
| `audiobiblio/dedupe.py` | `audiobiblio/dedupe_/matching.py` → see note |
| `audiobiblio/pipelines/` | `audiobiblio/library/pipelines/` |
| `audiobiblio/catalog.py` | `audiobiblio/library/catalog.py` |
| `audiobiblio/audioloader.py` | `audiobiblio/library/audioloader.py` |
| `audiobiblio/abs_client.py` | `audiobiblio/library/abs_client.py` |

`tags/`, `web/`, `cli.py`, `__main__.py`, `genre_taxonomy.json`, `rules.json`, `websites_mujrozhlas.json` stay where they are.

**Naming note:** the dedupe package is `audiobiblio/dedupe/` with the moved file at `audiobiblio/dedupe/matching.py`. Because a file `dedupe.py` and dir `dedupe/` can't coexist mid-move, do that move in two commands as shown below.

**Interfaces:**
- Produces the canonical import paths every later phase uses:
  `audiobiblio.core.config`, `audiobiblio.core.db.models`, `audiobiblio.core.db.session`, `audiobiblio.core.urls`, `audiobiblio.sources.discovery`, `audiobiblio.acquire.crawler`, `audiobiblio.acquire.scheduler`, `audiobiblio.dedupe.matching`, `audiobiblio.library.pipelines.*`

- [ ] **Step 1: Create package dirs and move files**

```bash
mkdir -p audiobiblio/sources audiobiblio/acquire audiobiblio/library
touch audiobiblio/sources/__init__.py audiobiblio/acquire/__init__.py audiobiblio/library/__init__.py

git mv audiobiblio/config.py        audiobiblio/core/config.py
git mv audiobiblio/logging_setup.py audiobiblio/core/logging_setup.py
git mv audiobiblio/ratelimit.py     audiobiblio/core/ratelimit.py
git mv audiobiblio/db               audiobiblio/core/db

git mv audiobiblio/discovery.py     audiobiblio/sources/discovery.py
git mv audiobiblio/mrz_inspector.py audiobiblio/sources/mrz_inspector.py
git mv audiobiblio/rapi.py          audiobiblio/sources/rapi.py

git mv audiobiblio/crawler.py      audiobiblio/acquire/crawler.py
git mv audiobiblio/downloader.py   audiobiblio/acquire/downloader.py
git mv audiobiblio/scheduler.py    audiobiblio/acquire/scheduler.py
git mv audiobiblio/availability.py audiobiblio/acquire/availability.py
git mv audiobiblio/jdownloader.py  audiobiblio/acquire/jdownloader.py

# dedupe.py -> dedupe/matching.py (two-step: file and dir share a name)
git mv audiobiblio/dedupe.py /tmp/dedupe_moving.py 2>/dev/null || mv audiobiblio/dedupe.py /tmp/dedupe_moving.py
mkdir -p audiobiblio/dedupe && touch audiobiblio/dedupe/__init__.py
mv /tmp/dedupe_moving.py audiobiblio/dedupe/matching.py
git add audiobiblio/dedupe/

git mv audiobiblio/pipelines     audiobiblio/library/pipelines
git mv audiobiblio/catalog.py    audiobiblio/library/catalog.py
git mv audiobiblio/audioloader.py audiobiblio/library/audioloader.py
git mv audiobiblio/abs_client.py audiobiblio/library/abs_client.py
```

- [ ] **Step 2: Rewrite imports project-wide**

Apply this exact mapping to every `.py` file under `audiobiblio/`, `tests/`, `migrations/`, and `scripts/` (both `from audiobiblio.X import …` and `import audiobiblio.X` forms, including relative imports inside moved files — e.g. `pipelines/*.py` files using `from ..db.models` become `from ...core.db.models` OR, simpler and preferred, convert relative imports in moved files to absolute `from audiobiblio.core.db.models import …`):

```bash
# macOS sed -i '' ; run from repo root
FILES=$(grep -rl "audiobiblio" --include="*.py" audiobiblio tests migrations scripts | sort -u)
for f in $FILES; do
  sed -i '' \
    -e 's/audiobiblio\.config/audiobiblio.core.config/g' \
    -e 's/audiobiblio\.logging_setup/audiobiblio.core.logging_setup/g' \
    -e 's/audiobiblio\.ratelimit/audiobiblio.core.ratelimit/g' \
    -e 's/audiobiblio\.db\./audiobiblio.core.db./g' \
    -e 's/from audiobiblio\.db import/from audiobiblio.core.db import/g' \
    -e 's/from audiobiblio import db/from audiobiblio.core import db/g' \
    -e 's/audiobiblio\.discovery/audiobiblio.sources.discovery/g' \
    -e 's/audiobiblio\.mrz_inspector/audiobiblio.sources.mrz_inspector/g' \
    -e 's/audiobiblio\.rapi/audiobiblio.sources.rapi/g' \
    -e 's/audiobiblio\.crawler/audiobiblio.acquire.crawler/g' \
    -e 's/audiobiblio\.downloader/audiobiblio.acquire.downloader/g' \
    -e 's/audiobiblio\.scheduler/audiobiblio.acquire.scheduler/g' \
    -e 's/audiobiblio\.availability/audiobiblio.acquire.availability/g' \
    -e 's/audiobiblio\.jdownloader/audiobiblio.acquire.jdownloader/g' \
    -e 's/audiobiblio\.dedupe import/audiobiblio.dedupe.matching import/g' \
    -e 's/audiobiblio\.pipelines/audiobiblio.library.pipelines/g' \
    -e 's/audiobiblio\.catalog/audiobiblio.library.catalog/g' \
    -e 's/audiobiblio\.audioloader/audiobiblio.library.audioloader/g' \
    -e 's/audiobiblio\.abs_client/audiobiblio.library.abs_client/g' \
    "$f"
done
```

Then fix relative imports *inside* moved files by hand — find them with:

```bash
grep -rn "^from \.\|^import \.\|from \.\." audiobiblio/core audiobiblio/sources audiobiblio/acquire audiobiblio/dedupe audiobiblio/library | grep -v "core/db\|tags/"
```

Convert each hit to the absolute path from the move map (e.g. in `acquire/crawler.py`, `from .db.session import …` → `from audiobiblio.core.db.session import …`). `tags/` internal relative imports (`from .diacritics import …`) are untouched — that package didn't move.

Guard against double-rewrites (e.g. `audiobiblio.core.config` accidentally becoming `audiobiblio.core.core.config` if sed runs twice): run the sed loop ONCE, then verify with `grep -rn "core\.core\|acquire\.acquire\|sources\.sources" audiobiblio/ tests/` → must return nothing.

- [ ] **Step 3: Fix pyproject entry points and Alembic**

In `pyproject.toml` `[project.scripts]`: `audioloader = "audiobiblio.library.audioloader:main"`. Check whether `audiobiblio/tag_fixer.py` exists; if it does not (only `tags/cli.py`), the stale `tag-fixer = "audiobiblio.tag_fixer:main"` entry is already broken — repoint it to `"audiobiblio.tags.cli:main"` if that module has a `main`, otherwise delete the entry and note it in the commit message.

In `migrations/env.py`, the sed in Step 2 already rewrote `audiobiblio.db.models` → `audiobiblio.core.db.models`; open the file and confirm.

- [ ] **Step 4: Import smoke check**

```bash
uv run python -c "
import audiobiblio.core.config, audiobiblio.core.db.models, audiobiblio.core.db.session
import audiobiblio.sources.discovery, audiobiblio.sources.rapi, audiobiblio.sources.mrz_inspector
import audiobiblio.acquire.crawler, audiobiblio.acquire.downloader, audiobiblio.acquire.scheduler, audiobiblio.acquire.availability
import audiobiblio.dedupe.matching, audiobiblio.library.catalog, audiobiblio.library.pipelines.ingest
import audiobiblio.tags.writer, audiobiblio.web.app, audiobiblio.cli
print('ALL IMPORTS OK')
"
```

Expected: `ALL IMPORTS OK`. Fix any `ModuleNotFoundError`/`ImportError` by consulting the move map (these are always a missed rewrite, never a reason to change behavior).

- [ ] **Step 5: Update test imports and run suite**

`tests/dedupe/test_matching.py`: change `from audiobiblio.dedupe import` → `from audiobiblio.dedupe.matching import` (the sed already did this — verify).

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Verify CLI and web still boot**

```bash
uv run audiobiblio --help
uv run python -c "from audiobiblio.web.app import create_app; create_app()" 2>&1 | tail -1
```

Expected: help text renders listing the 14 commands; app factory constructs without traceback. (If the factory function has a different name, check `audiobiblio/web/app.py` and use the actual name — do not rename it in this task.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: restructure into core/sources/acquire/dedupe/library modules"
```

---

### Task 6: Import-linter contract — module boundaries become law

**Files:**
- Modify: `pyproject.toml` (import-linter config)
- Test: `tests/test_architecture.py`

**Interfaces:**
- Produces: `lint-imports` passing = the dependency rule from the spec §3. Every later task must keep it green.

- [ ] **Step 1: Write the failing test**

Create `tests/test_architecture.py`:

```python
"""Module-boundary contract: web -> (acquire|library) -> (sources|dedupe|tags) -> core.

Runs import-linter as a subprocess so `uv run pytest` is the single gate.
"""
import subprocess


def test_import_contracts():
    result = subprocess.run(
        ["uv", "run", "lint-imports"], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"import-linter violations:\n{result.stdout}\n{result.stderr}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_architecture.py -v`
Expected: FAIL (no `[tool.importlinter]` config exists yet, lint-imports exits non-zero).

- [ ] **Step 3: Add the contract**

Append to `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "audiobiblio"

[[tool.importlinter.contracts]]
name = "Layered architecture"
type = "layers"
layers = [
  "audiobiblio.web",
  "audiobiblio.cli",
  "audiobiblio.acquire | audiobiblio.library",
  "audiobiblio.sources | audiobiblio.dedupe | audiobiblio.tags",
  "audiobiblio.core",
]
containers = []
exhaustive = false
```

- [ ] **Step 4: Run and resolve violations**

Run: `uv run lint-imports`

If violations appear (e.g. `sources.discovery` importing from `acquire`, or `core` importing `tags`), they are real findings. Resolution policy, in order of preference: (a) the import is only for a type hint → move under `if TYPE_CHECKING:`; (b) a shared helper sits in the wrong layer → move the helper down to the lowest layer that needs it (commit separately with `refactor:`); (c) a genuine upward dependency that can't be untangled mechanically → record it in `pyproject.toml` under the contract's `ignore_imports = [...]` with a `# TODO(phase2)` comment AND create `docs/decisions/` note. Do NOT reorder the layers to make violations vanish.

Then: `uv run pytest tests/test_architecture.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_architecture.py
git commit -m "feat: import-linter layer contract enforcing module boundaries"
```

---

### Task 7: Provenance groundwork — `metadata_values` table + resolver (real TDD)

Spec §2: DB is source of truth; every metadata field carries provenance (scraped / file / enriched / manual) and resolution precedence. This task builds the storage + pure resolution function. Wiring into tag-writing happens in Phase 4 — do not touch `tags/` here.

**Files:**
- Modify: `audiobiblio/core/db/models.py` (append `FieldOrigin`, `MetadataValue`)
- Create: `audiobiblio/core/provenance.py`
- Create: `migrations/versions/<generated>_add_metadata_values.py`
- Test: `tests/core/test_provenance.py`

**Interfaces:**
- Produces:
  - `audiobiblio.core.db.models.FieldOrigin` — str-Enum: `SCRAPED`, `FILE`, `ENRICHED`, `MANUAL`
  - `audiobiblio.core.db.models.MetadataValue` — columns: `entity_type: str(20)`, `entity_id: int`, `field: str(50)`, `value: Optional[str(4000)]`, `origin: FieldOrigin`, `source: str(100)`, `observed_at: datetime`; unique on `(entity_type, entity_id, field, origin, source)`
  - `audiobiblio.core.provenance.resolve_field(candidates: Sequence[MetadataValue]) -> MetadataValue | None` — precedence MANUAL > ENRICHED > FILE > SCRAPED; ties broken by newest `observed_at`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_provenance.py`:

```python
from datetime import datetime

from audiobiblio.core.db.models import FieldOrigin, MetadataValue
from audiobiblio.core.provenance import resolve_field


def _mv(origin: FieldOrigin, value: str, observed: str, source: str = "test") -> MetadataValue:
    return MetadataValue(
        entity_type="episode",
        entity_id=1,
        field="title",
        value=value,
        origin=origin,
        source=source,
        observed_at=datetime.fromisoformat(observed),
    )


def test_manual_beats_everything():
    winner = resolve_field([
        _mv(FieldOrigin.SCRAPED, "scraped title", "2026-07-01T00:00:00"),
        _mv(FieldOrigin.MANUAL, "my title", "2020-01-01T00:00:00"),
        _mv(FieldOrigin.ENRICHED, "dbk title", "2026-07-02T00:00:00"),
    ])
    assert winner.value == "my title"


def test_enriched_beats_file_and_scraped():
    winner = resolve_field([
        _mv(FieldOrigin.FILE, "file title", "2026-07-02T00:00:00"),
        _mv(FieldOrigin.ENRICHED, "dbk title", "2026-07-01T00:00:00"),
        _mv(FieldOrigin.SCRAPED, "scraped", "2026-07-02T00:00:00"),
    ])
    assert winner.value == "dbk title"


def test_same_origin_newest_wins():
    winner = resolve_field([
        _mv(FieldOrigin.SCRAPED, "old scrape", "2026-01-01T00:00:00"),
        _mv(FieldOrigin.SCRAPED, "new scrape", "2026-07-01T00:00:00", source="recrawl"),
    ])
    assert winner.value == "new scrape"


def test_empty_candidates_returns_none():
    assert resolve_field([]) is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/core/test_provenance.py -v`
Expected: FAIL with `ImportError` (`FieldOrigin` not defined).

- [ ] **Step 3: Add model + resolver**

Append to `audiobiblio/core/db/models.py`:

```python
class FieldOrigin(str, Enum):
    """Where a metadata value came from — precedence: MANUAL > ENRICHED > FILE > SCRAPED."""
    SCRAPED = "scraped"    # source website / feed metadata
    FILE = "file"          # read from existing file tags
    ENRICHED = "enriched"  # external enrichment (databazeknih, RAPI)
    MANUAL = "manual"      # user-edited; never overwritten automatically


class MetadataValue(Base):
    """One observed value for one metadata field of one entity, with provenance.

    The DB is the source of truth (spec §2): file tags are projections.
    Current effective value = provenance.resolve_field() over an entity's rows.
    """
    __tablename__ = "metadata_values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)  # "work" | "episode"
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(50))  # "title", "author", "narrator", ...
    value: Mapped[Optional[str]] = mapped_column(String(4000))
    origin: Mapped[FieldOrigin] = mapped_column(SAEnum(FieldOrigin), index=True)
    source: Mapped[str] = mapped_column(String(100))  # "mujrozhlas", "databazeknih", "user", file path…
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field", "origin", "source",
                         name="uq_metadata_value_provenance"),
        Index("ix_metadata_values_entity_field", "entity_type", "entity_id", "field"),
    )
```

Create `audiobiblio/core/provenance.py`:

```python
"""Provenance resolution: which observed value wins for a metadata field.

Precedence (spec §2): MANUAL > ENRICHED > FILE > SCRAPED; ties -> newest observed_at.
Manual edits therefore can never be silently overwritten by automatic values.
"""
from __future__ import annotations

from typing import Optional, Sequence

from audiobiblio.core.db.models import FieldOrigin, MetadataValue

_ORIGIN_RANK: dict[FieldOrigin, int] = {
    FieldOrigin.SCRAPED: 1,
    FieldOrigin.FILE: 2,
    FieldOrigin.ENRICHED: 3,
    FieldOrigin.MANUAL: 4,
}


def resolve_field(candidates: Sequence[MetadataValue]) -> Optional[MetadataValue]:
    """Return the winning value among all observed values for one field."""
    if not candidates:
        return None
    return max(candidates, key=lambda v: (_ORIGIN_RANK[v.origin], v.observed_at))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/core/test_provenance.py -v`
Expected: PASS

- [ ] **Step 5: Generate and check the migration**

```bash
uv run alembic revision --autogenerate -m "add metadata_values"
```

Open the generated file in `migrations/versions/` and verify: `down_revision` is `'584f34ff6085'`; upgrade creates exactly `metadata_values` with the unique constraint and index (delete any unrelated autogenerated noise — if autogenerate proposes dropping/altering ANY existing table, remove those ops and note it). Then:

```bash
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head
```

Expected: both directions clean against the dev SQLite DB.

- [ ] **Step 6: Full suite**

Run: `uv run pytest -q`
Expected: all PASS (including architecture contract — provenance.py lives in core, imports only core).

- [ ] **Step 7: Commit**

```bash
git add audiobiblio/core/db/models.py audiobiblio/core/provenance.py migrations/versions/ tests/core/test_provenance.py
git commit -m "feat: metadata_values table + provenance resolution (DB as source of truth)"
```

---

### Task 8: Archive mining → docs/dead-ends + docs/decisions, delete archive/

The 83 files in `archive/` are prior iterations. Lessons get extracted into permanent docs; then the directory is deleted (git history keeps the bytes). The four good reference docs move into module docs.

**Files:**
- Create: `docs/dead-ends/README.md`, `docs/dead-ends/0001-*.md` … (one file per distinct lesson found)
- Create: `docs/decisions/0001-modular-monolith.md`, `docs/decisions/0002-db-source-of-truth.md`
- Create: `docs/modules/tags/` — move `archive/NAMING_CONVENTION.md`, `archive/GENRE_TAXONOMY_README.md`, `archive/TAG_ROLE_FIXES.md` here; move `archive/CHANGELOG.md` → `docs/CHANGELOG-pre-redesign.md`
- Delete: `archive/` (entire directory)

**Interfaces:**
- Consumes: `archive/` contents (read before deleting).
- Produces: the dead-end record format all future failed experiments use.

- [ ] **Step 1: Create the dead-end template and index**

Create `docs/dead-ends/README.md`:

```markdown
# Dead Ends — the anti-library

Each file records one approach that was tried and abandoned, so it is never
unknowingly retried. Format per record:

- **What we tried** — one paragraph
- **Why it failed** — the concrete failure, with error/symptom if known
- **Don't retry unless** — the condition that would invalidate the lesson
- **Where the code was** — git ref or archive path (pre-deletion)

Add a record BEFORE deleting any failed experiment. Numbered `NNNN-slug.md`.
```

- [ ] **Step 2: Mine the archive**

Read each of these and extract lessons (this is a judgment task — read the file headers/docstrings and the version-to-version diffs where informative):

- `archive/audioloader_v20250910v003_broken.py` — why is it marked broken? Diff against v002: `diff archive/audioloader_v20250910v002.py archive/audioloader_v20250910v003_broken.py | head -100`. Write `docs/dead-ends/0001-audioloader-v003.md`.
- `archive/download_sites_v*.py` (13 versions) — what approach did the series abandon? (The live replacement is `sources/discovery.py` + `acquire/downloader.py`.) Write `docs/dead-ends/0002-download-sites-scripts.md` summarizing why monolithic per-site scripts were replaced by DB-backed queue + plugins.
- `archive/metadata*.py` / `archive/metadata_fetch*.py` — what enrichment approach was dropped? Write `docs/dead-ends/0003-metadata-fetch.md`.
- `archive/deprecated/` and `archive/experiments/` — list contents (`ls -la`), skim each file's docstring, add records only where a real lesson exists (don't fabricate; if a file is just an old copy, it needs no record).
- `archive/CHANGELOG.md` — contains real fix history (Dec 2025 chapter-title fix etc.); move, don't delete: `git mv archive/CHANGELOG.md docs/CHANGELOG-pre-redesign.md`.

Each dead-end record follows the README format above and is 10–25 lines. Also cross-check the user's memory of known mistakes if present in the repo docs — `docs/dead-ends` should include the known critical rules: dual-source ingest problems and "episode-level URLs only" (from prior sessions) if evidence for them appears in the archived code.

- [ ] **Step 3: Write the two seed decision records**

Create `docs/decisions/0001-modular-monolith.md`:

```markdown
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
```

Create `docs/decisions/0002-db-source-of-truth.md`:

```markdown
# 0002 — Metadata database is the source of truth; ID3 tags are projections

**Date:** 2026-07-02 · **Status:** accepted

Every metadata field is stored as observed values with provenance
(scraped/file/enriched/manual + timestamp) in `metadata_values`; the effective
value is computed by `core.provenance.resolve_field` (MANUAL > ENRICHED > FILE
> SCRAPED, ties -> newest). File tags are written FROM the DB, never trusted
over it. Consequence: conflicts are resolved once, in the DB, and cannot be
reintroduced by file operations. ABS gets metadata pushed; folder layout is an
export format, not a data model.
```

- [ ] **Step 4: Move the reference docs and delete archive/**

```bash
mkdir -p docs/modules/tags
git mv archive/NAMING_CONVENTION.md      docs/modules/tags/NAMING_CONVENTION.md
git mv archive/GENRE_TAXONOMY_README.md  docs/modules/tags/GENRE_TAXONOMY_README.md
git mv archive/TAG_ROLE_FIXES.md         docs/modules/tags/TAG_ROLE_FIXES.md
git mv archive/CHANGELOG.md              docs/CHANGELOG-pre-redesign.md
git rm -r archive/
```

Before `git rm`, run a final safety sweep: `grep -rn "import archive\|from archive" audiobiblio/ scripts/ tests/` → must return nothing.

- [ ] **Step 5: Run suite (nothing should reference archive)**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: mine archive/ into dead-ends + decisions, delete archive"
```

---

### Task 9: Module documentation pages + workflows.md

**Files:**
- Create: `docs/modules/core.md`, `docs/modules/sources.md`, `docs/modules/acquire.md`, `docs/modules/tags.md`, `docs/modules/dedupe.md`, `docs/modules/library.md`, `docs/modules/web.md`
- Create: `docs/workflows.md`
- Modify: `docs/README.md` (link the new pages)
- Modify: `README.md` (repo root — point to docs/, update the project description and the new module layout)

**Interfaces:**
- Consumes: the restructured tree from Task 5; spec §3–§5.

- [ ] **Step 1: Write the seven module pages**

Each page follows this exact skeleton (fill from the actual code — module docstrings, public functions, CLI commands visible in `audiobiblio/cli.py`; document what EXISTS today, with a short "Planned (phase N)" list from the spec):

```markdown
# <module> — <one-line purpose>

**Layer:** <position in the import-linter contract; what it may import>
**Standalone use:** <the CLI command(s) or `python -m` invocation that works without the web server, or "library-only">

## Responsibilities
<3-6 bullets, present tense, current reality>

## Public interface
<the functions/classes other modules are allowed to use — name, signature, one line each>

## Files
<table: file — purpose>

## Planned (which phase)
<bullets from the spec, marked with phase numbers>
```

For `docs/modules/tags.md`, link the three moved reference docs (NAMING_CONVENTION.md, GENRE_TAXONOMY_README.md, TAG_ROLE_FIXES.md) rather than duplicating their content.

- [ ] **Step 2: Write `docs/workflows.md`**

Copy spec §4 (the six workflows: daily loop, upgrades, import/unsorted, enrichment, gaps, DB↔ID3 sync) and mark each step with its current status: `[works today]`, `[partial: <what>]`, or `[phase N]`. This is the living document; the spec stays frozen.

- [ ] **Step 3: Link everything from docs/README.md and update root README.md**

`docs/README.md`: replace the "*(populated during Phase 1)*" placeholders with real links to the seven module pages, workflows.md, decisions/, dead-ends/.

Root `README.md`: update the module layout section (the old flat layout is gone), keep install/run instructions accurate (`uv sync`, `uv run audiobiblio --help`, `uv run pytest`).

- [ ] **Step 4: Commit**

```bash
git add docs/ README.md
git commit -m "docs: module pages, living workflows doc, updated READMEs"
```

---

### Task 10: Phase 1 verification gate

**Files:** none created — this is the exit checklist.

- [ ] **Step 1: Full suite + contract**

```bash
uv run pytest -q && uv run lint-imports
```

Expected: all tests PASS, contract KEPT.

- [ ] **Step 2: End-to-end smoke on real config (read-only)**

```bash
uv run audiobiblio --help
uv run audiobiblio paths
uv run audiobiblio target-list
uv run alembic current
```

Expected: all four run without traceback; `alembic current` shows the new metadata_values revision as head. (`target-list` exercises config + DB + models through the new module paths.)

- [ ] **Step 3: Web smoke**

```bash
uv run uvicorn audiobiblio.web.app:create_app --factory --port 8765 &
sleep 3 && curl -s http://localhost:8765/health ; kill %1
```

Expected: health endpoint responds. (If the factory name or health route differs, use the actual ones from `audiobiblio/web/app.py`.)

- [ ] **Step 4: Docker build check**

Run: `docker build -t audiobiblio-test . 2>&1 | tail -5` — the Dockerfile does `pip install -e .`; the restructure must not have broken packaging (`[tool.setuptools.packages.find] include = ["audiobiblio*"]` still matches). If Docker isn't running locally, verify packaging instead with `uv run python -m build --wheel 2>&1 | tail -3` or `uv pip install -e . --dry-run`.

- [ ] **Step 5: Report**

Summarize to the user: test counts, contract status, any dead-ends recorded, any violations parked with `ignore_imports`, anything discovered mid-restructure that needs a Phase 2 decision.

---

## Later Phases (outlined — each gets its own plan when its predecessor lands)

- **Phase 2 — Daily loop:** per-target `approval_mode` column on CrawlTarget (auto|review) + candidate state; Inbox page (approve/reject, bulk); new UI shell in infosoud_web design language (base template, vanilla CSS tokens, HTMX + SSE wiring); Console with inbox/failures/health; Sources & Downloads pages rebuilt. *Daily use starts here.*
- **Phase 3 — Quality & upgrades:** quality scoring over Asset fields; upgrade decision function (auto if strictly better, ad-suspect if duration mismatch > tolerance); tag carry-over on replace; 30-day trash; Dedupe page with clusters + merge.
- **Phase 4 — Sync & import:** DB↔ID3 sync engine writing projections from `metadata_values`; unsorted-inbox + legacy library scanner (three buckets); Import page; conflict queue UI.
- **Phase 5 — Enrichment & gaps:** databazeknih client with caching; expected-episode lists; WANTED episodes, priority watching, cross-source gap matching; gap report + "hunt now".
- **Phase 6 — Polish:** absorb scripts/abs_*.py into `library/`; System page; mobile refinements; auth decision.
