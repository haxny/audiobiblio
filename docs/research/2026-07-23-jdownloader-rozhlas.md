# jDownloader Czech Radio plugin analysis (2026-07)

> Agent research, 2026-07-23. Sources: jDownloader SVN trunk mirror + yt-dlp.

## Conclusions

1. **jDownloader has exactly ONE Czech Radio plugin**: the crawler/decrypter `RozhlasCz.java`. No mujrozhlas.cz plugin, no croaod.cz plugin, no dedicated hoster. Downloading is delegated to generic `directhttp` (plain HTTP) and `GenericM3u8` (HLS) plugins.
2. **Why jD got 192 kbps where yt-dlp gave 128 kbps**: jD adds **every non-DASH URL** found in the page (no quality selection at all), including `linkType: "download"` links to `portal.rozhlas.cz/sites/default/files/audios/<md5>.mp3` — the Drupal-attached **originals, actually 192 kbps** even though the embedded JSON labels them `bitrate: 128`. yt-dlp feeds that false `bitrate` into its format sorter, so it can rank a croaod 128k stream above the portal original. Lesson: **prefer `linkType: "download"` / portal URLs and never trust the `bitrate` field.**
3. **For mujrozhlas.cz, yt-dlp is strictly superior** (jD has nothing): `MujRozhlasIE` uses api.mujrozhlas.cz incl. serial/show pagination.
4. The jD plugin is **stable/unmaintained**: trunk revision `r49388`, last functional change July 2024. Monthly/quarterly re-check is plenty.

## Plugin: `RozhlasCz.java` (decrypter)

- Live daily SVN mirror: `mycodedoesnotcompile2/jdownloader_mirror` → `svn_trunk/src/jd/plugins/decrypter/RozhlasCz.java` (official `mirror/jdownloader` is stale since 2024-09-27).
- URL pattern: `https?://(?:[a-z0-9]+\.)?rozhlas\.cz/([a-z0-9\-]+)\-(\d+)` (any subdomain; NOT mujrozhlas). Rate limit 1 req/s per host.
- Package title: `og:title` meta, fallback slug.

Four independent branches over the article HTML (results accumulated):

1. **Legacy prehravac embeds:** `https?://prehravac\.rozhlas\.cz/audio/(\d+)` → `https://media.rozhlas.cz/_audio/<id>.mp3` directly.
2. **Serial `<li><div part=...>` markup (older pages):** split on `(<li><div part=.*?</div></li>)`; per item: `part="(\d+)"` (track number), `title="([^"]+)"`, direct ` href="(https?://[^"]+\.mp3[^"]*)"` (catches `/sites/default/files/audios/` originals), else `https?://cros\d+://([a-f0-9\-]+)` → `https://croaod.cz/stream/<hash>.m4a/chunklist.m3u8`. Filename `<part>.<trackTitle>.<ext>`.
3. **Modern `data-player` JSON (current pages):** `data-player='(\{.*?\})'` → `data.playlist[]` with `title, duration, audioLinks[] {linkType, variant, duration, bitrate, url, sizeInBytes}`. jD takes all audioLinks except `*.mpd` (DASH skipped since 2022). Rich metadata in `playlist[].meta.ga`: `contentId`, `contentAuthor`, `contentCreator` (station), `contentSerialPart`, `contentSerialAllParts`, `contentSerialName` (`<uuid>: <title>` — uuid usable against api.mujrozhlas.cz). jD ignores meta; yt-dlp uses it.
4. **Single-podcast direct file:** `"(https?://dvojka\.rozhlas\.cz/sites/default/files/audios/[a-f0-9]+\.mp3[^"]*)"` (hardcoded dvojka — generalize to any subdomain in our port).

Quality-variant selection: **none** (user picks in LinkGrabber). Serial expansion: only what's in one page; no API pagination.

## yt-dlp comparison (`yt_dlp/extractor/rozhlas.py`)

| Capability | jDownloader | yt-dlp |
|---|---|---|
| prehravac → media.rozhlas.cz | yes (finds embeds in articles) | yes (direct URLs only) |
| `data-player` JSON | yes | yes (`RozhlasVltavaIE`) |
| Legacy `<li><div part=` markup | yes | no |
| Article-HTML direct `audios/*.mp3` anchors | yes | **no** — only audioLinks JSON |
| DASH | skipped | supported |
| Quality choice | none (take all) | sorter using unreliable `bitrate` |
| mujrozhlas.cz | nothing | full API with pagination |
| Metadata | filename only | full via meta.ga + API |
| 429 handling | 1 req/s throttle | RetryManager with sleep |

## What to adopt into our Python crawler

1. **`*.rozhlas.cz` article handler**: parse `data-player` JSON (`html.unescape` + `json.loads`); rank `linkType == "download"` (portal original) > plain mp3 > HLS > DASH; ignore `bitrate` field — ffprobe/size-math for truth. Plus raw-HTML scan for `/sites/default/files/audios/[a-f0-9]+\.mp3` on any subdomain; keep both legacy fallbacks (prehravac, `cros\d://hash`).
2. **Metadata** from `meta.ga` (author, station, part numbering) and `data.series.title`; uuid in `contentSerialName` links to the API serial for playlist completion.
3. **mujrozhlas / multi-part**: follow yt-dlp's API design; per episode still prefer the portal download variant.
4. **Politeness**: 1 req/s per host + retry-with-sleep on 429.

## Watch-for-updates

- raw: `https://raw.githubusercontent.com/mycodedoesnotcompile2/jdownloader_mirror/main/svn_trunk/src/jd/plugins/decrypter/RozhlasCz.java`
  (change detector: grep `$Revision:` != **49388**)
- history: `https://github.com/mycodedoesnotcompile2/jdownloader_mirror/commits/main/svn_trunk/src/jd/plugins/decrypter/RozhlasCz.java`
- yt-dlp: `https://github.com/yt-dlp/yt-dlp/commits/master/yt_dlp/extractor/rozhlas.py`
