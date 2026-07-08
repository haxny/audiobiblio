"""
abs — Audiobookshelf API client and metadata-synchronisation utilities.

Auth precedence (highest to lowest):
1. Explicit constructor args (base_url, api_key)
2. Config values loaded via load_config():
   a. AUDIOBIBLIO_ABS_URL / AUDIOBIBLIO_ABS_API_KEY env vars  (new canonical)
   b. config.yaml  abs_url / abs_api_key fields
   c. Legacy ABS_URL / ABS_API_KEY env vars  (absorbed into config precedence)
3. Direct legacy ABS_URL / ABS_API_KEY env vars when no Config object is passed
   (backward-compat for standalone scripts)

Quick start:
    # With config (preferred)
    from audiobiblio.core.config import load_config
    from audiobiblio.library.abs import AbsClient
    client = AbsClient.from_config(load_config())

    # Legacy / standalone script usage (reads ABS_URL / ABS_API_KEY from env)
    client = AbsClient.from_config()
"""
from __future__ import annotations

import os
from typing import Callable

import requests
import structlog
import urllib3

from audiobiblio.core.ratelimit import RateLimiter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = structlog.get_logger()

# Shared 10 rps limiter — all AbsClient instances share this by default so
# concurrent use across the application still respects the ABS rate limit.
_ABS_RATE_LIMITER = RateLimiter(rate=10, burst=10)

# Audio-file extensions that indicate a filename was used verbatim as a title.
# Used by the sync path (abs_sync_metadata.py needs_fix / build_patch_for_item).
_BAD_TITLE_EXTS = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus")

# Push-path extensions: original abs_push_metadata.py build_patch (line 90) checked
# only 3 extensions, not the broader 6-extension set used by abs_sync_metadata.py.
_PUSH_BAD_TITLE_EXTS = (".mp3", ".m4a", ".m4b")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AbsClient:
    """Thin Audiobookshelf API client with Bearer auth and rate limiting.

    Args:
        base_url:   ABS base URL (e.g. ``https://audio.book.cz``).
        api_key:    ABS API token.
        verify_ssl: Passed to requests; False by default because ABS instances
                    are commonly behind self-signed reverse proxies.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        verify_ssl: bool = False,
        _limiter: RateLimiter | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )
        self._limiter = _limiter if _limiter is not None else _ABS_RATE_LIMITER

    @classmethod
    def from_config(cls, cfg: object | None = None) -> "AbsClient":
        """Create from a Config object; falls back to canonical then legacy env vars.

        Precedence:
        1. ``cfg.abs_url`` / ``cfg.abs_api_key``  (themselves sourced from
           AUDIOBIBLIO_ABS_URL → config.yaml → ABS_URL, per load_config())
        2. ``AUDIOBIBLIO_ABS_URL`` / ``AUDIOBIBLIO_ABS_API_KEY`` env vars (canonical)
        3. ``ABS_URL`` / ``ABS_API_KEY`` env vars directly (scripts' legacy convention)
        """
        base_url = ""
        api_key = ""

        if cfg is not None:
            base_url = getattr(cfg, "abs_url", "") or ""
            api_key = getattr(cfg, "abs_api_key", "") or ""

        # Canonical env vars take priority over legacy ABS_URL / ABS_API_KEY
        if not base_url:
            base_url = os.environ.get("AUDIOBIBLIO_ABS_URL", "")
        if not api_key:
            api_key = os.environ.get("AUDIOBIBLIO_ABS_API_KEY", "")

        # Fallback to legacy env vars (used by scripts directly)
        if not base_url:
            base_url = os.environ.get("ABS_URL", "")
        if not api_key:
            api_key = os.environ.get("ABS_API_KEY", "")

        return cls(base_url=base_url, api_key=api_key)

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    def get_libraries(self) -> list[dict]:
        """GET /api/libraries → list of library dicts."""
        self._limiter.wait()
        r = self._session.get(f"{self.base_url}/api/libraries", timeout=10)
        r.raise_for_status()
        return r.json().get("libraries", [])

    def get_library_items(self, library_id: str, batch_size: int = 50) -> list[dict]:
        """GET /api/libraries/{id}/items → all items (auto-paginated).

        Returns ALL pages combined into a single list.  This differs from the
        original scripts (abs_push_metadata.py, abs_sync_metadata.py) which
        processed items page by page in the main loop; here pagination is
        handled internally so callers receive a single flat list.
        """
        items: list[dict] = []
        page = 0
        while True:
            self._limiter.wait()
            r = self._session.get(
                f"{self.base_url}/api/libraries/{library_id}/items",
                params={"limit": batch_size, "page": page, "expanded": 1},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("results", [])
            items.extend(results)
            if len(items) >= data.get("total", 0) or not results:
                break
            page += 1
        return items

    def get_item(self, item_id: str) -> dict:
        """GET /api/items/{id} → full item detail including audio file tags."""
        self._limiter.wait()
        r = self._session.get(f"{self.base_url}/api/items/{item_id}", timeout=15)
        r.raise_for_status()
        return r.json()

    def patch_item_media(self, item_id: str, patch: dict) -> dict:
        """PATCH /api/items/{id}/media → update item metadata."""
        self._limiter.wait()
        r = self._session.patch(
            f"{self.base_url}/api/items/{item_id}/media",
            json=patch,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def trigger_scan(self, library_id: str) -> bool:
        """POST /api/libraries/{id}/scan → trigger a library rescan."""
        self._limiter.wait()
        try:
            r = self._session.post(
                f"{self.base_url}/api/libraries/{library_id}/scan",
                timeout=30,
            )
            r.raise_for_status()
            log.info("abs_scan_triggered", library_id=library_id)
            return True
        except requests.RequestException as exc:
            log.error("abs_scan_failed", library_id=library_id, error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Metadata sync utilities  (ported from scripts/abs_sync_metadata.py)
# ---------------------------------------------------------------------------


def needs_fix(item: dict, force_title: bool = False) -> bool:
    """Return True if a lightweight list item needs metadata fixes.

    Ported from scripts/abs_sync_metadata.py lines 77-102.

    Rules:
    - Skip ebook-only items (``numAudioFiles == 0``).
    - ``force_title=True`` always returns True.
    - Title ends with an audio extension → True.
    - Title is blank → True.
    - Narrators list is empty → True.
    """
    meta = item.get("media", {}).get("metadata", {})
    title = meta.get("title", "")
    narrators = meta.get("narrators") or []
    num_audio = item.get("media", {}).get("numAudioFiles", 0)

    if num_audio == 0:
        return False

    if force_title:
        return True

    if title.endswith(_BAD_TITLE_EXTS):
        return True
    if not title.strip():
        return True
    if not narrators:
        return True

    return False


def _extract_tags(item_detail: dict) -> dict:
    """Extract metadata from the first audio file's metaTags.

    Ported from scripts/abs_sync_metadata.py lines 105-134.

    Tag mapping:
    - ``tagAlbum``        → title
    - ``tagAlbumArtist`` / ``tagArtist`` → authorName
    - ``tagPerformer`` / ``tagComposer`` → narrator
      (line 124: ABS doesn't map PERFORMER automatically)
    - ``tagPublisher``   → publisher
    - ``tagDate[:4]``    → publishedYear
    """
    audio_files = item_detail.get("media", {}).get("audioFiles", [])
    if not audio_files:
        return {}

    tags = audio_files[0].get("metaTags", {})
    result: dict = {}

    if tags.get("tagAlbum"):
        result["title"] = tags["tagAlbum"]

    if tags.get("tagAlbumArtist"):
        result["authorName"] = tags["tagAlbumArtist"]
    elif tags.get("tagArtist"):
        result["authorName"] = tags["tagArtist"]

    # PERFORMER → narrator  (scripts/abs_sync_metadata.py line 124)
    performer = tags.get("tagPerformer") or tags.get("tagComposer")
    if performer and performer != result.get("authorName"):
        result["narrator"] = performer

    if tags.get("tagPublisher"):
        result["publisher"] = tags["tagPublisher"]

    if tags.get("tagDate"):
        result["publishedYear"] = tags["tagDate"][:4]

    return result


def build_patch_for_item(
    item_detail: dict, force_title: bool = False
) -> tuple[dict | None, str]:
    """Build a PATCH payload from audio tags embedded in a full item detail.

    Combines :func:`_extract_tags` and the patch-building logic ported from
    scripts/abs_sync_metadata.py lines 137-174.

    Args:
        item_detail: Full item response from ``GET /api/items/{id}``.
        force_title: If True, always overwrite title even if the current value
                     looks valid.

    Returns:
        A ``(patch, reason)`` tuple where:

        - ``({"metadata": {...}}, "patch")``  — a patch was built.
        - ``(None, "no_change")`` — tags exist but nothing needs updating.
        - ``(None, "no_tags")``  — no audio file tags found at all.

        The reason strings map to the original script counters:
        ``"no_tags"`` → ``total_no_tags``;
        ``"no_change"`` → ``total_skipped``.
    """
    tags = _extract_tags(item_detail)
    if not tags:
        return None, "no_tags"

    meta = item_detail.get("media", {}).get("metadata", {})
    patch: dict = {}

    # Title  (scripts/abs_sync_metadata.py lines 142-155)
    tag_title = tags.get("title")
    current_title = meta.get("title", "")
    if tag_title:
        is_bad = (
            current_title.endswith(_BAD_TITLE_EXTS)
            or not current_title.strip()
            or "/" in current_title
            or "\\" in current_title
        )
        if force_title or is_bad:
            if tag_title != current_title:
                patch["title"] = tag_title

    # Narrator  (scripts/abs_sync_metadata.py lines 157-160)
    tag_narrator = tags.get("narrator")
    current_narrators = meta.get("narrators") or []
    if tag_narrator and not current_narrators:
        patch["narrators"] = [tag_narrator]

    # Publisher  (scripts/abs_sync_metadata.py lines 162-164)
    tag_publisher = tags.get("publisher")
    if tag_publisher and not meta.get("publisher"):
        patch["publisher"] = tag_publisher

    # Published year  (scripts/abs_sync_metadata.py lines 166-168)
    tag_year = tags.get("publishedYear")
    if tag_year and not meta.get("publishedYear"):
        patch["publishedYear"] = tag_year

    if not patch:
        return None, "no_change"
    return {"metadata": patch}, "patch"


# ---------------------------------------------------------------------------
# Push loop  (core ported from scripts/abs_push_metadata.py)
# ---------------------------------------------------------------------------


def _build_push_patch(abs_item: dict, local_meta: dict, force: bool = False) -> dict | None:
    """Compare ABS metadata with locally-sourced metadata; return patch if needed.

    Ported from scripts/abs_push_metadata.py lines 82-127.

    NAS path resolution (abs_push_metadata.py lines 168-169) is intentionally
    absent: it depends on ABS_ROOT → LOCAL_ROOT mount mapping that is
    deployment-specific and therefore stays in the calling script.

    Args:
        abs_item:   Lightweight ABS item dict (from list endpoint).
        local_meta: Dict with keys: title, narrators, genres, publisher,
                    publishedYear, description.  Matches ``to_abs_metadata()``
                    output from abs_generate_metadata.py.
        force:      Overwrite existing ABS fields even when non-empty.

    Returns:
        ``{"metadata": {...}}`` payload, or ``None`` if nothing changed.
    """
    current = abs_item.get("media", {}).get("metadata", {})
    patch: dict = {}

    # Title  (abs_push_metadata.py lines 88-93)
    # NOTE: the push script used only 3 extensions (.mp3, .m4a, .m4b); the sync
    # script used all 6.  Use _PUSH_BAD_TITLE_EXTS here to stay faithful to the
    # original abs_push_metadata.py build_patch().
    local_title = local_meta.get("title", "")
    current_title = current.get("title", "")
    if local_title and (
        force or not current_title or current_title.endswith(_PUSH_BAD_TITLE_EXTS)
    ):
        if local_title != current_title:
            patch["title"] = local_title

    # Narrators  (abs_push_metadata.py lines 95-99)
    local_narr = local_meta.get("narrators", [])
    current_narr = current.get("narrators") or []
    if local_narr and (force or not current_narr):
        if set(local_narr) != set(current_narr):
            patch["narrators"] = local_narr

    # Genres  (abs_push_metadata.py lines 101-105)
    local_genres = local_meta.get("genres", [])
    current_genres = current.get("genres") or []
    if local_genres and (force or not current_genres):
        if set(local_genres) != set(current_genres):
            patch["genres"] = local_genres

    # Publisher  (abs_push_metadata.py lines 107-110)
    local_pub = local_meta.get("publisher", "")
    if local_pub and (force or not current.get("publisher")):
        if local_pub != current.get("publisher"):
            patch["publisher"] = local_pub

    # Published year  (abs_push_metadata.py lines 112-115)
    local_year = local_meta.get("publishedYear", "")
    if local_year and (force or not current.get("publishedYear")):
        if local_year != current.get("publishedYear"):
            patch["publishedYear"] = local_year

    # Description  (abs_push_metadata.py lines 117-119)
    local_desc = local_meta.get("description", "")
    if local_desc and len(local_desc) > 100 and (
        force or not current.get("description")
    ):
        patch["description"] = local_desc

    if not patch:
        return None
    return {"metadata": patch}


def push_missing_metadata(
    client: AbsClient,
    library_id: str,
    local_metadata_fn: Callable[[dict], dict | None],
    dry_run: bool = True,
    force: bool = False,
) -> dict:
    """Core loop: fetch all library items and push local metadata to ABS.

    Loop shape ported from scripts/abs_push_metadata.py lines 153-233.
    NAS path resolution and audio-tag reading (lines 168-192) remain in the
    calling script because they require ABS_ROOT→LOCAL_ROOT mount knowledge
    that is deployment-specific and cannot live in the library module.

    Args:
        client:            Authenticated :class:`AbsClient`.
        library_id:        ABS library ID to process.
        local_metadata_fn: ``(item: dict) -> dict | None`` — given a lightweight
                           ABS item dict, return local metadata (keys: title,
                           narrators, genres, publisher, publishedYear,
                           description) or ``None`` when no local data is
                           available (path not found, permission error, etc.).
        dry_run:           Count changes without applying them when True.
        force:             Overwrite existing ABS fields when True.

    Returns:
        Stats dict with keys ``updated``, ``skipped``, ``no_meta``, ``errors``.
    """
    items = client.get_library_items(library_id)
    stats: dict[str, int] = {
        "updated": 0,
        "skipped": 0,
        "no_meta": 0,
        "errors": 0,
    }

    for item in items:
        try:
            local_meta = local_metadata_fn(item)
        except Exception as exc:
            log.error(
                "abs_push_local_meta_error",
                item_id=item.get("id"),
                error=str(exc),
            )
            stats["errors"] += 1
            continue

        if local_meta is None:
            stats["no_meta"] += 1
            continue

        patch = _build_push_patch(item, local_meta, force)
        if not patch:
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["updated"] += 1
            continue

        try:
            client.patch_item_media(item["id"], patch)
            stats["updated"] += 1
        except requests.RequestException as exc:
            log.error(
                "abs_push_patch_error",
                item_id=item.get("id"),
                error=str(exc),
            )
            stats["errors"] += 1

    return stats
