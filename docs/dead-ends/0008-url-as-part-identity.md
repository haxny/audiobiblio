# 0008 — URL as episode identity for mujrozhlas multi-part books

**Status:** dead end. Never re-introduce URL-keyed dedup/identity for mujrozhlas entries.

## The wrong assumption

That a mujrozhlas episode is identified by its page URL. It is not: mujrozhlas
embeds ALL parts of a serialized book on ONE page (e.g. Příběh služebnice,
12 parts, one `/cetba-s-hvezdickou/margaret-attwoodova-pribeh-sluzebnice` page).
The parts share URL and title; the only per-part identity is the yt-dlp entry
`id` (→ `ext_id`) plus `episode_number`.

## How it failed — three layers, three releases (all found live by the user)

1. **0.7.1** — tier-3 fuzzy dedupe collapsed identically-titled parts
   (fix: distinct URLs never fuzzy-collapse — necessary but insufficient).
2. **0.7.2** — `classify_probe` DROPPED the per-entry `id`/`episode_number`
   (fix: `ext_id` flows probe → dedupe → ingest, conflict guards on all tiers).
3. **0.7.4** — parts never reached ingest at all: BOTH crawler loops
   (`crawl_target` container loop, `_expand_series` children loop) deduped
   discovered entries by URL alone, and the paste flow (`/api/v1/ingest/url`)
   took `entries[0]` only. 12 parts in, 1 episode out — regardless of the
   0.7.2 identity fix downstream.

Symptom to recognize it by: a freshly crawled multi-part book shows ONE
episode with N assets, or "jediná epizoda" after pasting a book URL.

## The rule

- Dedup keys for discovered entries are **ext_id-first**; URL is the fallback
  for entries without media ids (listing/container pages).
- An entry carrying `ext_id` IS an episode — never deep-probe it again,
  never drop it for sharing a URL with its siblings or with the crawl target.
- Downloads select the part via `--playlist-items <episode_number>` on the
  shared page URL (downloader already does this).

Regression locks: `tests/acquire/test_multipart_expansion.py`,
`tests/web/test_ingest_url_multipart.py`, plus the 0.7.2 conflict-guard tests
in `tests/dedupe/`.
