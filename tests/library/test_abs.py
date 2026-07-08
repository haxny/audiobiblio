"""
Tests for audiobiblio.library.abs — AbsClient and metadata-sync utilities.

TDD cases:
1.  AbsClient sets Bearer auth header on session
2.  from_config() uses config values when present
3.  from_config() falls back to legacy ABS_URL/ABS_API_KEY env vars
3b. from_config() canonical AUDIOBIBLIO_ABS_URL beats legacy ABS_URL (no cfg)
4.  needs_fix() → True when title ends with audio extension
5.  needs_fix() → True when narrators list is empty
6.  needs_fix() → False for ebook-only item (numAudioFiles=0)
7.  needs_fix() → False for item with good title + narrator
8.  build_patch_for_item(): tagAlbum extracted as title patch
9.  build_patch_for_item(): tagPerformer extracted as narrator patch
10. build_patch_for_item(): returns (None, "no_change") when item is already clean
10b.build_patch_for_item(): returns (None, "no_tags") when no audioFiles
11. get_libraries() sends GET to correct URL, parses response
12. patch_item_media() sends PATCH with correct payload
13. _build_push_patch(): title-bad-by-extension → replaced
14. _build_push_patch(): good title → kept
15. _build_push_patch(): narrator/publisher/year/description fill-when-empty
16. _build_push_patch(): only 3 push extensions (.mp3,.m4a,.m4b) — .flac kept
17. push_missing_metadata(): dry_run=True → no PATCH calls
18. push_missing_metadata(): patch called only for items with a patch
19. push_missing_metadata(): error on one item doesn't kill the loop
"""
from __future__ import annotations

import os

import pytest

from audiobiblio.library.abs import (
    AbsClient,
    _build_push_patch,
    build_patch_for_item,
    needs_fix,
    push_missing_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockResponse:
    """Minimal requests.Response stand-in."""

    def __init__(self, data: object, status: int = 200) -> None:
        self._data = data
        self.status_code = status

    def json(self) -> object:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)


def _item_with_audio(
    title: str = "Good Title",
    narrators: list[str] | None = None,
    num_audio: int = 1,
) -> dict:
    """Build a minimal lightweight ABS list item."""
    return {
        "id": "item-1",
        "media": {
            "numAudioFiles": num_audio,
            "metadata": {
                "title": title,
                "narrators": narrators if narrators is not None else ["Jan Vlček"],
            },
        },
    }


def _item_detail(
    current_title: str = "bad.mp3",
    narrators: list[str] | None = None,
    tag_album: str = "Správný Titulek",
    tag_performer: str = "Jan Vlček",
    tag_publisher: str = "",
    tag_date: str = "",
) -> dict:
    """Build a minimal full item detail (with audioFiles/metaTags)."""
    return {
        "id": "item-1",
        "media": {
            "metadata": {
                "title": current_title,
                "narrators": narrators or [],
                "publisher": "",
                "publishedYear": "",
            },
            "audioFiles": [
                {
                    "metaTags": {
                        "tagAlbum": tag_album,
                        "tagPerformer": tag_performer,
                        "tagPublisher": tag_publisher,
                        "tagDate": tag_date,
                    }
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# 1. AbsClient sets Bearer auth header
# ---------------------------------------------------------------------------

def test_client_sets_bearer_header() -> None:
    client = AbsClient("http://abs.example.com", "super-secret-key")
    auth = client._session.headers.get("Authorization", "")
    assert auth == "Bearer super-secret-key"


# ---------------------------------------------------------------------------
# 2. from_config uses config values when present
# ---------------------------------------------------------------------------

def test_client_from_config_uses_config_values() -> None:
    class FakeCfg:
        abs_url = "http://from-config.example.com"
        abs_api_key = "config-key"

    client = AbsClient.from_config(FakeCfg())
    assert client.base_url == "http://from-config.example.com"
    assert client._session.headers["Authorization"] == "Bearer config-key"


# ---------------------------------------------------------------------------
# 3. from_config falls back to legacy env vars
# ---------------------------------------------------------------------------

def test_client_from_config_falls_back_to_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABS_URL", "http://legacy-env.example.com")
    monkeypatch.setenv("ABS_API_KEY", "legacy-env-key")

    client = AbsClient.from_config()  # no cfg passed
    assert client.base_url == "http://legacy-env.example.com"
    assert "legacy-env-key" in client._session.headers.get("Authorization", "")


# ---------------------------------------------------------------------------
# 3b. from_config: canonical env var beats legacy env var
# ---------------------------------------------------------------------------

def test_canonical_env_beats_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIOBIBLIO_ABS_URL/_API_KEY (canonical) win over legacy ABS_URL/ABS_API_KEY."""
    monkeypatch.setenv("AUDIOBIBLIO_ABS_URL", "http://canonical.example.com")
    monkeypatch.setenv("AUDIOBIBLIO_ABS_API_KEY", "canonical-key")
    monkeypatch.setenv("ABS_URL", "http://legacy.example.com")
    monkeypatch.setenv("ABS_API_KEY", "legacy-key")

    client = AbsClient.from_config()  # no cfg passed
    assert client.base_url == "http://canonical.example.com"
    assert client._session.headers["Authorization"] == "Bearer canonical-key"


# ---------------------------------------------------------------------------
# 4. needs_fix → True for bad title (audio extension)
# ---------------------------------------------------------------------------

def test_needs_fix_bad_title_extension() -> None:
    item = _item_with_audio(title="some_audiobook.mp3", narrators=["Jan Vlček"])
    assert needs_fix(item) is True


def test_needs_fix_bad_title_m4b_extension() -> None:
    item = _item_with_audio(title="file.m4b", narrators=["Jan Vlček"])
    assert needs_fix(item) is True


def test_needs_fix_empty_title() -> None:
    item = _item_with_audio(title="", narrators=["Jan Vlček"])
    assert needs_fix(item) is True


# ---------------------------------------------------------------------------
# 5. needs_fix → True when narrators missing
# ---------------------------------------------------------------------------

def test_needs_fix_missing_narrator() -> None:
    item = _item_with_audio(title="Good Title", narrators=[])
    assert needs_fix(item) is True


def test_needs_fix_none_narrators() -> None:
    # ABS sometimes returns null instead of []
    item = _item_with_audio(title="Good Title", narrators=None)
    item["media"]["metadata"]["narrators"] = None
    assert needs_fix(item) is True


# ---------------------------------------------------------------------------
# 6. needs_fix → False for ebook-only
# ---------------------------------------------------------------------------

def test_needs_fix_ebook_only() -> None:
    item = _item_with_audio(title="bad.mp3", narrators=[], num_audio=0)
    assert needs_fix(item) is False


# ---------------------------------------------------------------------------
# 7. needs_fix → False when item is already complete
# ---------------------------------------------------------------------------

def test_needs_fix_complete_item() -> None:
    item = _item_with_audio(title="Dobrý Titulek", narrators=["Jan Vlček"])
    assert needs_fix(item) is False


def test_needs_fix_force_title() -> None:
    """force_title=True forces a fix even on a complete item."""
    item = _item_with_audio(title="Dobrý Titulek", narrators=["Jan Vlček"])
    assert needs_fix(item, force_title=True) is True


# ---------------------------------------------------------------------------
# 8. build_patch_for_item: tagAlbum extracted as title patch
# ---------------------------------------------------------------------------

def test_build_patch_for_item_patches_bad_title() -> None:
    detail = _item_detail(
        current_title="bad_file.mp3",
        tag_album="Správný Titulek",
        tag_performer="Jan Vlček",
    )
    patch, reason = build_patch_for_item(detail)
    assert reason == "patch"
    assert patch is not None
    assert patch["metadata"]["title"] == "Správný Titulek"


def test_build_patch_for_item_patches_slash_title() -> None:
    """Title containing '/' is treated as bad (path leaked into title)."""
    detail = _item_detail(
        current_title="audiobooks/book/file.mp3",
        tag_album="Dobrý Název",
    )
    patch, reason = build_patch_for_item(detail)
    assert reason == "patch"
    assert patch is not None
    assert patch["metadata"]["title"] == "Dobrý Název"


# ---------------------------------------------------------------------------
# 9. build_patch_for_item: tagPerformer extracted as narrator
# ---------------------------------------------------------------------------

def test_build_patch_for_item_patches_missing_narrator() -> None:
    detail = _item_detail(
        current_title="Dobrý Titulek",
        narrators=[],
        tag_album="",          # no title change needed
        tag_performer="Jana Nováková",
    )
    # No tagAlbum → title won't be patched; narrator should be patched
    patch, reason = build_patch_for_item(detail)
    assert reason == "patch"
    assert patch is not None
    assert patch["metadata"]["narrators"] == ["Jana Nováková"]


def test_build_patch_for_item_uses_composer_when_no_performer() -> None:
    """tagComposer used as narrator fallback when tagPerformer absent."""
    detail = {
        "id": "x",
        "media": {
            "metadata": {
                "title": "Dobrý Titulek",
                "narrators": [],
                "publisher": "",
                "publishedYear": "",
            },
            "audioFiles": [
                {
                    "metaTags": {
                        "tagAlbum": "",
                        "tagComposer": "Pavel Kříž",
                    }
                }
            ],
        },
    }
    patch, reason = build_patch_for_item(detail)
    assert reason == "patch"
    assert patch is not None
    assert patch["metadata"]["narrators"] == ["Pavel Kříž"]


# ---------------------------------------------------------------------------
# 10. build_patch_for_item → None when item already clean
# ---------------------------------------------------------------------------

def test_build_patch_for_item_no_patch_when_clean() -> None:
    """If title is fine and narrator is set, no patch needed → reason='no_change'."""
    detail = _item_detail(
        current_title="Správný Titulek",
        narrators=["Jan Vlček"],
        tag_album="Správný Titulek",  # same as current → no title patch
        tag_performer="Jan Vlček",    # same performer, but narrator already set
    )
    patch, reason = build_patch_for_item(detail)
    # narrators already set → no narrator patch; title same → no title patch
    assert patch is None
    assert reason == "no_change"


def test_build_patch_for_item_no_audio_files() -> None:
    """Items without audioFiles return (None, 'no_tags')."""
    detail = {
        "id": "x",
        "media": {
            "metadata": {"title": "bad.mp3", "narrators": []},
            "audioFiles": [],
        },
    }
    patch, reason = build_patch_for_item(detail)
    assert patch is None
    assert reason == "no_tags"


# ---------------------------------------------------------------------------
# 11. get_libraries — correct URL, response parsed
# ---------------------------------------------------------------------------

def test_get_libraries_makes_correct_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AbsClient("http://abs.example.com", "key")
    monkeypatch.setattr(client._limiter, "wait", lambda: None)

    captured: dict = {}

    def fake_get(url: str, **kw: object) -> _MockResponse:
        captured["url"] = url
        return _MockResponse({"libraries": [{"id": "lib-1", "name": "Fiction"}]})

    monkeypatch.setattr(client._session, "get", fake_get)

    libs = client.get_libraries()
    assert captured["url"] == "http://abs.example.com/api/libraries"
    assert len(libs) == 1
    assert libs[0]["name"] == "Fiction"


# ---------------------------------------------------------------------------
# 12. patch_item_media — sends PATCH with correct payload
# ---------------------------------------------------------------------------

def test_patch_item_media_sends_correct_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AbsClient("http://abs.example.com", "key")
    monkeypatch.setattr(client._limiter, "wait", lambda: None)

    captured: dict = {}

    def fake_patch(url: str, **kw: object) -> _MockResponse:
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _MockResponse({"id": "item-1"})

    monkeypatch.setattr(client._session, "patch", fake_patch)

    payload = {"metadata": {"title": "Nový Titulek"}}
    result = client.patch_item_media("item-1", payload)
    assert captured["url"] == "http://abs.example.com/api/items/item-1/media"
    assert captured["json"] == payload
    assert result == {"id": "item-1"}


# ---------------------------------------------------------------------------
# 13-16. _build_push_patch — ported from abs_push_metadata.py build_patch()
# ---------------------------------------------------------------------------

def _push_abs_item(
    title: str = "Good Title",
    narrators: list[str] | None = None,
    publisher: str = "",
    published_year: str = "",
    description: str = "",
) -> dict:
    """Lightweight ABS list item as seen by the push loop."""
    return {
        "id": "item-1",
        "media": {
            "metadata": {
                "title": title,
                "narrators": narrators if narrators is not None else [],
                "publisher": publisher,
                "publishedYear": published_year,
                "description": description,
            },
        },
    }


def test_push_patch_replaces_title_bad_by_extension() -> None:
    """Filename-as-title (.mp3) is replaced by local title."""
    abs_item = _push_abs_item(title="chapter01.mp3")
    local_meta = {"title": "Krásný Nový Svět"}
    patch = _build_push_patch(abs_item, local_meta)
    assert patch is not None
    assert patch["metadata"]["title"] == "Krásný Nový Svět"


def test_push_patch_keeps_good_title() -> None:
    """A non-empty, non-extension title is kept (no force)."""
    abs_item = _push_abs_item(title="Ruční Kurátorský Titulek")
    local_meta = {"title": "Jiný Titulek Z Tagů"}
    patch = _build_push_patch(abs_item, local_meta)
    assert patch is None


def test_push_patch_fills_empty_fields_only() -> None:
    """narrator/publisher/year/description fill only when ABS field is empty."""
    abs_item = _push_abs_item(
        title="Dobrý Titulek",
        narrators=[],
        publisher="",
        published_year="",
        description="",
    )
    long_desc = "x" * 150  # original requires len > 100
    local_meta = {
        "title": "Dobrý Titulek",  # same → no title patch
        "narrators": ["Jan Vlček"],
        "publisher": "Tympanum",
        "publishedYear": "2019",
        "description": long_desc,
    }
    patch = _build_push_patch(abs_item, local_meta)
    assert patch is not None
    md = patch["metadata"]
    assert md["narrators"] == ["Jan Vlček"]
    assert md["publisher"] == "Tympanum"
    assert md["publishedYear"] == "2019"
    assert md["description"] == long_desc
    assert "title" not in md

    # Now the same but with ABS fields already populated → nothing patched
    filled = _push_abs_item(
        title="Dobrý Titulek",
        narrators=["Existing Narrator"],
        publisher="Existing Pub",
        published_year="2001",
        description="already has a description",
    )
    assert _build_push_patch(filled, local_meta) is None


def test_push_patch_short_description_not_pushed() -> None:
    """Descriptions of 100 chars or fewer are never pushed (original len>100 rule)."""
    abs_item = _push_abs_item(title="Dobrý Titulek", description="")
    local_meta = {"title": "Dobrý Titulek", "description": "short blurb"}
    assert _build_push_patch(abs_item, local_meta) is None


def test_push_patch_flac_title_not_treated_as_bad() -> None:
    """Push path uses only (.mp3, .m4a, .m4b) — original abs_push_metadata.py
    line 90 — so a .flac title is NOT replaced (unlike the sync-side 6-ext set)."""
    abs_item = _push_abs_item(title="album.flac")
    local_meta = {"title": "Skutečný Titulek"}
    assert _build_push_patch(abs_item, local_meta) is None
    # Sanity: sync-side needs_fix DOES flag .flac titles
    sync_item = _item_with_audio(title="album.flac", narrators=["Jan Vlček"])
    assert needs_fix(sync_item) is True


# ---------------------------------------------------------------------------
# 17-19. push_missing_metadata loop
# ---------------------------------------------------------------------------

class _FakePushClient:
    """AbsClient stand-in recording patch_item_media calls."""

    def __init__(self, items: list[dict], fail_ids: set[str] | None = None) -> None:
        self._items = items
        self._fail_ids = fail_ids or set()
        self.patch_calls: list[tuple[str, dict]] = []

    def get_library_items(self, library_id: str, batch_size: int = 50) -> list[dict]:
        return self._items

    def patch_item_media(self, item_id: str, payload: dict) -> dict:
        if item_id in self._fail_ids:
            import requests
            raise requests.RequestException(f"boom for {item_id}")
        self.patch_calls.append((item_id, payload))
        return {"id": item_id}


def _push_items_fixture() -> list[dict]:
    """Two patchable items (bad .mp3 titles) + one clean item."""
    bad1 = _push_abs_item(title="a.mp3")
    bad1["id"] = "bad-1"
    bad2 = _push_abs_item(title="b.m4b")
    bad2["id"] = "bad-2"
    clean = _push_abs_item(title="Perfektní Titulek")
    clean["id"] = "clean-1"
    return [bad1, bad2, clean]


def test_push_missing_metadata_dry_run_no_patch_calls() -> None:
    client = _FakePushClient(_push_items_fixture())
    stats = push_missing_metadata(
        client, "lib-1",
        local_metadata_fn=lambda item: {"title": f"Local {item['id']}"},
        dry_run=True,
    )
    assert client.patch_calls == []
    assert stats["updated"] == 2   # both bad titles counted as would-update
    assert stats["skipped"] == 1   # clean item skipped


def test_push_missing_metadata_patches_only_items_with_patch() -> None:
    client = _FakePushClient(_push_items_fixture())
    stats = push_missing_metadata(
        client, "lib-1",
        local_metadata_fn=lambda item: {"title": f"Local {item['id']}"},
        dry_run=False,
    )
    patched_ids = [c[0] for c in client.patch_calls]
    assert patched_ids == ["bad-1", "bad-2"]  # clean item never PATCHed
    assert stats["updated"] == 2
    assert stats["skipped"] == 1
    assert stats["errors"] == 0


def test_push_missing_metadata_error_does_not_kill_loop() -> None:
    """PATCH failure on one item counts an error and continues — same as the
    original abs_push_metadata.py main loop (lines 220-233)."""
    client = _FakePushClient(_push_items_fixture(), fail_ids={"bad-1"})
    stats = push_missing_metadata(
        client, "lib-1",
        local_metadata_fn=lambda item: {"title": f"Local {item['id']}"},
        dry_run=False,
    )
    assert stats["errors"] == 1
    assert stats["updated"] == 1               # bad-2 still processed
    assert [c[0] for c in client.patch_calls] == ["bad-2"]


def test_push_missing_metadata_local_meta_fn_error_isolated() -> None:
    """local_metadata_fn raising counts an error and continues — mirrors the
    original's try/except around build_metadata (lines 182-190)."""
    items = _push_items_fixture()

    def flaky_meta(item: dict) -> dict:
        if item["id"] == "bad-1":
            raise OSError("permission denied")
        return {"title": f"Local {item['id']}"}

    client = _FakePushClient(items)
    stats = push_missing_metadata(client, "lib-1", flaky_meta, dry_run=False)
    assert stats["errors"] == 1
    assert stats["updated"] == 1
    assert [c[0] for c in client.patch_calls] == ["bad-2"]
