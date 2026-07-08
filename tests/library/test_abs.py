"""
Tests for audiobiblio.library.abs — AbsClient and metadata-sync utilities.

TDD cases:
1.  AbsClient sets Bearer auth header on session
2.  from_config() uses config values when present
3.  from_config() falls back to legacy ABS_URL/ABS_API_KEY env vars
4.  needs_fix() → True when title ends with audio extension
5.  needs_fix() → True when narrators list is empty
6.  needs_fix() → False for ebook-only item (numAudioFiles=0)
7.  needs_fix() → False for item with good title + narrator
8.  build_patch_for_item(): tagAlbum extracted as title patch
9.  build_patch_for_item(): tagPerformer extracted as narrator patch
10. build_patch_for_item(): returns None when item is already clean
11. get_libraries() sends GET to correct URL, parses response
12. patch_item_media() sends PATCH with correct payload
"""
from __future__ import annotations

import os

import pytest

from audiobiblio.library.abs import (
    AbsClient,
    build_patch_for_item,
    needs_fix,
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
    patch = build_patch_for_item(detail)
    assert patch is not None
    assert patch["metadata"]["title"] == "Správný Titulek"


def test_build_patch_for_item_patches_slash_title() -> None:
    """Title containing '/' is treated as bad (path leaked into title)."""
    detail = _item_detail(
        current_title="audiobooks/book/file.mp3",
        tag_album="Dobrý Název",
    )
    patch = build_patch_for_item(detail)
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
    patch = build_patch_for_item(detail)
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
    patch = build_patch_for_item(detail)
    assert patch is not None
    assert patch["metadata"]["narrators"] == ["Pavel Kříž"]


# ---------------------------------------------------------------------------
# 10. build_patch_for_item → None when item already clean
# ---------------------------------------------------------------------------

def test_build_patch_for_item_no_patch_when_clean() -> None:
    """If title is fine and narrator is set, no patch needed."""
    detail = _item_detail(
        current_title="Správný Titulek",
        narrators=["Jan Vlček"],
        tag_album="Správný Titulek",  # same as current → no title patch
        tag_performer="Jan Vlček",    # same performer, but narrator already set
    )
    patch = build_patch_for_item(detail)
    # narrators already set → no narrator patch; title same → no title patch
    assert patch is None


def test_build_patch_for_item_no_audio_files() -> None:
    """Items without audioFiles return None."""
    detail = {
        "id": "x",
        "media": {
            "metadata": {"title": "bad.mp3", "narrators": []},
            "audioFiles": [],
        },
    }
    assert build_patch_for_item(detail) is None


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
