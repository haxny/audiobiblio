"""rAPI schedule/stations client for the live recorder (HTTP mocked)."""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from audiobiblio.acquire.live import schedule as sch
from audiobiblio.acquire.live.schedule import (
    RapiClient, ScheduleItem, pick_stream, stream_urls,
)

FIX = Path(__file__).parents[2] / "fixtures" / "live"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_from_rapi_parses_block_item():
    page = json.loads((FIX / "schedule_raw_page.json").read_text())
    it = ScheduleItem.from_rapi(page["data"][0])
    assert it.headline.startswith("Návštěva u Daniela Wiesnera")
    assert it.station_id == "e26a14b0-35d5-3b55-a6c8-679d2c6e0526"
    assert it.has_show is False and it.has_episode is False and it.has_audio is False
    assert (it.till - it.since).total_seconds() > 0
    linked = ScheduleItem.from_rapi(page["data"][-1])
    assert linked.has_show is True


def test_stream_urls_prefers_aac_high_then_mp3():
    stations = json.loads((FIX / "stations.json").read_text())["data"]
    urls = stream_urls(stations)
    assert urls["e26a14b0-35d5-3b55-a6c8-679d2c6e0526"] == "https://rozhlas.stream/ddur_high.aac"
    assert urls["0134ce01-8684-3556-b568-f208392ac0bd"] == "https://rozhlas.stream/vltava_high.aac"


def test_pick_stream_skips_flac_and_low():
    links = [
        {"linkType": "directstream", "url": "https://amp.cesnet.cz:8443/cro-d-dur.flac", "bitrate": 32},
        {"linkType": "directstream", "url": "https://rozhlas.stream/ddur_low.aac", "bitrate": 64},
        {"linkType": "directstream", "url": "https://rozhlas.stream/ddur.mp3", "bitrate": 128},
    ]
    assert pick_stream(links) == "https://rozhlas.stream/ddur.mp3"
    assert pick_stream([]) is None


def test_fetch_schedule_paginates_sends_user_agent_and_backs_off_on_429(monkeypatch):
    calls: list = []
    sleeps: list = []
    page1 = {"meta": {"count": 3}, "data": [_raw("a"), _raw("b")]}
    page2 = {"meta": {"count": 3}, "data": [_raw("c")]}
    answers = [urllib.error.HTTPError("u", 429, "Too Many", {}, None), page1, page2]

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        ans = answers.pop(0)
        if isinstance(ans, Exception):
            raise ans
        return _Resp(json.dumps(ans).encode())

    monkeypatch.setattr(sch.urllib.request, "urlopen", fake_urlopen)
    client = RapiClient(sleep=sleeps.append, gap_s=1.0)
    items = client.fetch_schedule(date(2026, 11, 7), days=1)
    assert [i.id for i in items] == ["a", "b", "c"]
    assert all("audiobiblio" in r.get_header("User-agent") for r in calls)
    assert "page%5Boffset%5D=2" in calls[-1].full_url
    assert any(s >= 30 for s in sleeps)          # 429 back-off
    assert sleeps.count(1.0) >= 1                 # ≤ 1 req/s gap between pages


def test_fetch_gives_up_after_retries(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.HTTPError("u", 503, "down", {}, None)
    monkeypatch.setattr(sch.urllib.request, "urlopen", boom)
    client = RapiClient(sleep=lambda s: None, gap_s=0, retries=2)
    with pytest.raises(RuntimeError):
        client.fetch_schedule(date(2026, 11, 7), days=1)


def _raw(i: str) -> dict:
    return {"type": "scheduleEpisode", "id": i,
            "attributes": {"title": "t", "description": "", "since": "2026-11-07T08:00:00+01:00",
                           "till": "2026-11-07T09:00:00+01:00", "mirroredShow": {"title": "h"},
                           "audioLinks": []},
            "relationships": {"station": {"data": {"type": "station",
                                                   "id": "e26a14b0-35d5-3b55-a6c8-679d2c6e0526"}}}}
