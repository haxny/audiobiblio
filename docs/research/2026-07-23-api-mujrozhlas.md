# api.mujrozhlas.cz — Research Report (2026-07)

> Agent research, 2026-07-23. Verified live against the API and station pages.

## TL;DR conclusions

1. **api.mujrozhlas.cz is the single source of truth** — mujrozhlas.cz, the station Drupal sites (wave.rozhlas.cz etc.), the embedded players, and the podcast RSS all read from it ("rAPI"). No separate serialAPI.
2. **Legacy numeric ID lives in `meta.ga.contentId`** (e.g. `12090594`), not in attributes. It is **not filterable**, but the numeric ID in a rozhlas.cz article URL resolves via `/serial-redirect/{id}` and `/show-redirect/{id}` (301 → UUID).
3. **Expiry = `audioLinks[].playableTill`** (episode `till` is just broadcast end). Expired episodes stay in the API with **empty `audioLinks`** — that's the availability signal.
4. **The 128 vs 192 kbps mystery is solved: the API's `bitrate` field lies.** The Muklové download mp3 is labeled `bitrate: 128` but the actual file (portal.rozhlas.cz) is **192 kbps / 48 kHz** (verified with ffprobe; also `sizeInBytes*8/duration` = 192,006 bps). There is no separate 192k link — same URL. **Always prefer `linkType: "download"` and compute real bitrate from `sizeInBytes/duration`.** yt-dlp reports 128 because it trusts the label (or picks the genuinely-128k HLS/dash croaod stream when present).

## 1. Endpoint catalog

Root `/` returns `["Hi, my name is rAPI. How can I help you?"]`. Existing collections (all JSON:API: `meta.count`, `links.next/last`, `data[]`):

| Endpoint | Count | Attributes | Relationships | audioLinks |
|---|---|---|---|---|
| `/episodes` | ~807k | `title, shortTitle, description, since, till, updated, part, asset, mirroredShow, mirroredSerial, audioRightsExtended, audioLinks` + `meta.ga.{contentCreator, contentId, baseId}` | show, serial, keywords, genres, stations | **yes** — `linkType` (download/ondemand), `variant` (mp3/hls/dash), `duration`, `bitrate`, `url`, `sizeInBytes` (download only), `playableTill` |
| `/serials` | ~7.2k | `title, shortTitle, totalParts, description, updated, lastEpisodeSince, playable, asset` | show, **episodes**, genres | no |
| `/shows` | ~1.4k | `showType, showContent, title, active, aired, podcast, priority, childFriendly, sortedBySeriesEpisodeNumber, description, shortDescription, asset, updated` | stations, serials, episodes, participants | no |
| `/stations` | 26 | `title, shortTitle, subtitle, color, code, priority, stationType, isAdHocStream, audioLinks, asset, logo*, contact` | — | yes (live streams: aac/mp3/dash, bitrates 32–160, `quality`) |
| `/schedule` | ~2.18M | `title, description, station_code, showPriority, showTimes, since, till, mirroredShow, asset, audioLinks` (type `scheduleEpisode`) | show, station, participants | mostly empty |
| `/topics` | 26 | `title, fulltext_keywords, position, description, asset` | episode | no |
| `/persons` | ~12k | `title, short_description, description, asset, assetHost, profile_id` | participation | no |
| `/genres` | 16 | `title` | — | no |
| `/keywords` | ~7.6k | `title` | — | no |

Non-existent: `/timespans`, `/aggregations`, `/podcasts`, `/participants`, `/featured`.
Bonus endpoints: `/rss/podcast/{show-uuid}` (podcast RSS, enclosures via `dts.podtrac.com/redirect.mp3/portal.rozhlas.cz/...`), `/show-redirect/{id}`, `/serial-redirect/{id}`.

## 2. Filtering, lookup, pagination

Nice property: bad filters return an explicit `{"message": "Field \"x\" is not filterable", "code": "non_filterable_field"}` — probing is cheap and safe.

**Works on `/episodes`:**
- `filter[title]=...` (exact) and `filter[title][like]=Muklové` (substring, diacritics-sensitive)
- `filter[since][ge]=2026-07-20`, `filter[since][le]=...` (date ranges)
- `filter[stations.id]={station-uuid}` (note plural — `station.id` fails)
- `sort=since`, `sort=-since`, `sort=title`
- `page[limit]=N`, `page[offset]=N` + `links.next` for cursorless paging

**Does NOT work on `/episodes`:** `till`, `updated`, `part`, `show.id`, `serial.id`, any legacy-id field. JSON:API `include=` is silently ignored (no `included` returned).

**Works on `/serials`:** `filter[title][like]`, `sort=title`. Not filterable: `since`, `lastEpisodeSince`, `playable`; `updated` not sortable.

**Works on `/schedule`:** `filter[since][ge]`; `station_code` not filterable.

**Lookups:** `/episodes/{uuid}`, `/serials/{uuid}` work. Serial → episodes via relationship link `https://api.mujrozhlas.cz/serials/{uuid}/episodes` (returns all parts). Also `/shows/{uuid}/episodes?sort=-since` works.

## 3. Expiry / availability

- **`audioLinks[].playableTill`** is the authoritative expiry. Present per-link.
- Episode `since`/`till` = broadcast window only. `updated` = last modification.
- The embedded player config additionally exposes `availability: {from, to}` per playlist item (UTC).
- Serial has `playable` (bool) + `lastEpisodeSince`.
- **After expiry the episode entity remains** but `audioLinks` becomes `[]` and `meta.ga` disappears. So "poslední šance" detection = episodes where `audioLinks` non-empty and `min(playableTill)` is near.
- `audioRightsExtended` (bool) also present.

## 4. Legacy ID mapping

- The numeric ID (`12090594`) is **`meta.ga.contentId`** on the episode. It's the Drupal node id when audio is portal-hosted; for croaod-hosted news clips `contentId` is the audio-asset UUID instead (same UUID as in the croaod URL).
- **Not filterable** — `filter[contentId]`, `filter[idec]`, `filter[legacyId]` all rejected. No `/episode-redirect` route.
- **Practical mapping paths:**
  - Article URL numeric suffix (e.g. `...-9626940`) → `GET /serial-redirect/9626940` → 301 to `https://www.mujrozhlas.cz/rapi/view/serial/{serial-uuid}` → fetch `/serials/{uuid}/episodes` and match `meta.ga.contentId` against ext_id.
  - `/show-redirect/{showNodeId}` works the same for shows.
  - Consecutive parts have consecutive contentIds — useful heuristic.

## 5. audioLinks details & airing grouping

- Variants: `mp3` (linkType `download`, has `sizeInBytes`), `hls` + `dash` (linkType `ondemand`, croaod.cz `stream/{asset-uuid}.m4a/playlist.m3u8|manifest.mpd`). Two hosting worlds: `portal.rozhlas.cz/sites/default/files/audios/{md5}.mp3` (Drupal) and `croaod.cz/download|stream/{uuid}` (AOD).
- `bitrate` is exposed but **unreliable for downloads** (labeled 128, actually 192; croaod news downloads go up to 256). Streams tend to be genuinely 128. Compute `sizeInBytes*8/duration` for truth.
- Some content is stream-only (e.g. current Vltava Saturnin reading: only hls+dash 128, no download — likely rights-restricted audiobooks on Vltava).
- **Airing grouping:** re-airings share **`meta.ga.contentId`**; `meta.ga.baseId` points to the base content's numeric id. Within one show the API dedupes (each part appears once), but *separate productions/readings* of the same book are distinct serial UUIDs (3 different Saturnin serials from 2020/2023/2024 + 2026). Same-recording detection across entities: match the audio-asset hash/UUID in `audioLinks.url`, or `contentId`. `mirroredSerial`/`mirroredShow` are denormalized display snapshots, not grouping keys.
- `/schedule` (2.18M rows) is the full airing log (every broadcast slot incl. reruns, filterable by `since`).

## 6. rozhlas.cz station-site architecture

- Station sites are legacy **Drupal 7** ("cro_soundmanager" module). The article embeds `<div class="mujRozhlasPlayer" data-player='{...}'>` whose JSON contains: `urlRapi: "https://api.mujrozhlas.cz"`, `urlAjaxApi: "https://mujrozhlas.cz"`, `embedId` (Drupal node id), `embedUrl` (`.../cro_soundmanager/files/{id}/serial_view` — HTML embed, not JSON), a full server-rendered `playlist[]` with per-part `title, since, part, duration, availability{from,to}, audioLinks[]` (identical objects to the API, incl. `playableTill`), `podcastLinks`, and `meta.ga.contentId`.
- **Same croaod/portal assets as the API — no second source, no hidden higher-bitrate URL.** The "192k from the station page" is simply the `download` mp3 whose real encoding is 192 kbps despite the `bitrate: 128` label.
- **Recommendation for audiobiblio:** always fetch the `linkType: "download"` URL from the API when present (highest-quality asset), verify bitrate from size/duration, and fall back to HLS only when no download link exists.
