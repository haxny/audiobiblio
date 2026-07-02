# 0003 — Ad-hoc databazeknih.cz HTML scraper for enrichment

**What we tried**

`archive/metadata_fetch.py` (and two versioned predecessors) scraped
databazeknih.cz by sending a search query with `requests`, parsing the HTML
response with BeautifulSoup, finding the first result link, fetching the book
page, and extracting title, author, publisher, year, description, and cover URL
via CSS selectors. It was designed to be called per-folder: pass `"Author -
Title"` and get back a JSON dict to feed into tag writes.

The paired `archive/metadata.py` (v002, v003) contained pure helper functions
(`build_album`, `build_id3_tags`, etc.) that assembled tag strings from a
metadata dict — also hand-written without a DB layer.

**Why it failed**

The scraper was fragile against site layout changes and returned inconsistent
data depending on which search result was first. It had no rate-limiting, no
caching, and no provenance tracking (so re-running could overwrite a manually
corrected field with a freshly scraped wrong value). There was also no handling
for the Czech/Slovak diacritics ambiguity in search queries: identical names
with and without accents could yield different results. The live approach
instead stores each enrichment value with a `source` + `fetched_at` column in
`metadata_values`, resolves conflicts via `core.provenance.resolve_field`, and
treats file tags as write-only projections of DB state.

**Don't retry unless**

You add provenance tracking (source + timestamp per field), rate limiting, and
deduplicate against existing DB values before writing — otherwise a re-run will
silently overwrite manual corrections.

**Where the code was**

`archive/metadata_fetch.py`, `archive/metadata_fetch v20250814v002.py`,
`archive/metadata_fetch v20250814v003.py`, `archive/metadata.py`,
`archive/metadata v20250814v002.py`, `archive/metadata v20250814v003.py`
(deleted with `archive/` in commit
`docs: mine archive/ into dead-ends + decisions, delete archive`).
