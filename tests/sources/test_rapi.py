"""Tests for rAPI show resolution, audio-link choice and recent-episode paging.

Network is stubbed at rapi._get; no request leaves the test.
"""
from __future__ import annotations

import pytest

from audiobiblio.sources import rapi

UUID = "01883023-cf70-3469-ba1b-0f4a3ba5a224"


class _Resp:
    def __init__(self, status=200, payload=None, location=""):
        self.status_code = status
        self._payload = payload or {}
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_uuid_cache():
    rapi._UUID_CACHE.clear()
    yield
    rapi._UUID_CACHE.clear()


@pytest.fixture()
def calls(monkeypatch):
    log = []

    def install(router):
        def fake_get(url, params=None, follow=True):
            log.append((url, params))
            return router(url, params)
        monkeypatch.setattr(rapi, "_get", fake_get)
        return log
    return install


def _ep(eid, links=True, cid="1", title="T"):
    return {"id": eid, "meta": {"ga": {"contentId": cid}},
            "attributes": {"title": title, "since": "2026-10-01T10:00:00+02:00",
                           "audioLinks": [{"linkType": "download", "variant": "mp3",
                                           "url": f"https://portal.rozhlas.cz/{eid}.mp3",
                                           "duration": 60}] if links else []}}


class TestResolveShowUuid:
    def test_node_url_resolves_via_show_redirect(self, calls):
        log = calls(lambda u, p: _Resp(301, location=f"https://www.mujrozhlas.cz/rapi/view/show/{UUID}"))
        assert rapi.resolve_show_uuid(["https://radiozurnal.rozhlas.cz/vyvar-9259685"]) == UUID
        assert log[0][0].endswith("/show-redirect/9259685")

    def test_slug_url_falls_back_to_unique_title_match(self, calls):
        calls(lambda u, p: _Resp(200, {"data": [{"id": UUID, "attributes": {"title": "VýVar"}}]}))
        assert rapi.resolve_show_uuid(["https://www.mujrozhlas.cz/vyvar"], name="VýVar") == UUID

    def test_ambiguous_title_is_not_guessed(self, calls):
        calls(lambda u, p: _Resp(200, {"data": [{"id": UUID}, {"id": "other"}]}))
        assert rapi.resolve_show_uuid(["https://www.mujrozhlas.cz/x"], name="Zprávy") is None

    def test_no_node_and_no_name_gives_none_without_requests(self, calls):
        log = calls(lambda u, p: _Resp(404))
        assert rapi.resolve_show_uuid(["https://www.mujrozhlas.cz/x"], name=None) is None
        assert log == []

    def test_paired_url_node_is_used(self, calls):
        calls(lambda u, p: _Resp(301, location=f"https://x/rapi/view/show/{UUID}"))
        urls = ["https://www.mujrozhlas.cz/vyvar", "https://radiozurnal.rozhlas.cz/vyvar-9259685"]
        assert rapi.resolve_show_uuid(urls) == UUID


    def test_resolved_uuid_is_cached(self, calls):
        log = calls(lambda u, p: _Resp(301, location=f"https://x/rapi/view/show/{UUID}"))
        urls = ["https://radiozurnal.rozhlas.cz/vyvar-9259685"]
        assert rapi.resolve_show_uuid(urls) == rapi.resolve_show_uuid(urls) == UUID
        assert len(log) == 1


class TestBestAudioUrl:
    def test_download_link_wins_over_hls(self):
        links = [{"variant": "hls", "url": "h"}, {"linkType": "download", "url": "d"}]
        assert rapi.best_audio_url(links) == "d"

    def test_hls_when_no_download(self):
        assert rapi.best_audio_url([{"variant": "dash", "url": "x"},
                                    {"variant": "hls", "url": "h"}]) == "h"

    def test_empty(self):
        assert rapi.best_audio_url([]) is None


class TestFetchRecentEpisodes:
    def test_stops_on_page_without_unknown_live_episode(self, calls):
        pages = {0: [_ep("a"), _ep("b")], 2: [_ep("c"), _ep("d")], 4: [_ep("e"), _ep("f")]}
        log = calls(lambda u, p: _Resp(200, {"data": pages[p["page[offset]"]]}))
        out = rapi.fetch_recent_episodes(UUID, is_known=lambda e: e["id"] in {"c", "d"},
                                         page_size=2, max_pages=10)
        assert [e["id"] for e in out] == ["a", "b"]
        assert len(log) == 2  # page 0 had news, page 2 was all known → stop
        assert log[0][1]["sort"] == "-since"

    def test_expired_episodes_are_skipped_and_end_paging(self, calls):
        calls(lambda u, p: _Resp(200, {"data": [_ep("a", links=False)]}))
        assert rapi.fetch_recent_episodes(UUID, is_known=lambda e: False, page_size=1) == []

    def test_max_pages_caps_requests(self, calls):
        log = calls(lambda u, p: _Resp(200, {"data": [_ep(str(p["page[offset]"]))]}))
        out = rapi.fetch_recent_episodes(UUID, is_known=lambda e: False, page_size=1, max_pages=3)
        assert len(out) == 3 and len(log) == 3

    def test_http_error_returns_what_was_collected(self, calls):
        def router(u, p):
            if p["page[offset]"] == 0:
                return _Resp(200, {"data": [_ep("a")]})
            raise rapi.requests.RequestException("boom")
        calls(router)
        out = rapi.fetch_recent_episodes(UUID, is_known=lambda e: False, page_size=1)
        assert [e["id"] for e in out] == ["a"]
