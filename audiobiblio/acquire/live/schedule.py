"""rAPI schedule + stations client for the live recorder.

`/schedule` cannot be filtered by station ("station.id is not filterable"),
so a day is fetched whole (~1,400 items, 3 pages of 500) and filtered
locally. Politeness: ≤ 1 request/s, long back-off on 429, explicit UA.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import structlog

log = structlog.get_logger()

API = "https://api.mujrozhlas.cz"
USER_AGENT = "audiobiblio-live-recorder/1.0 (personal archive; +https://github.com/haxny/audiobiblio)"
PAGE_LIMIT = 500
REQUEST_GAP_S = 1.05
BACKOFF_429_S = 30
RETRIES = 5
PRAGUE_OFFSET = "+01:00"  # rAPI accepts any offset; items carry their own


@dataclass(frozen=True)
class ScheduleItem:
    """One rAPI schedule slot, reduced to what detection needs.

    headline = mirroredShow.title when present (D-dur: the block or the
    composition; unlinked items: the real item title incl. 'N/M'), else
    attributes.title. since/till are timezone-aware.
    """
    id: str
    station_id: str
    since: datetime
    till: datetime
    title: str
    headline: str
    description: str
    has_show: bool
    has_episode: bool
    has_audio: bool

    @property
    def minutes(self) -> float:
        return (self.till - self.since).total_seconds() / 60

    @classmethod
    def from_rapi(cls, e: dict) -> "ScheduleItem":
        a = e.get("attributes") or {}
        rel = e.get("relationships") or {}

        def rel_id(name: str) -> str | None:
            return ((rel.get(name) or {}).get("data") or {}).get("id")

        title = a.get("title") or ""
        return cls(
            id=e["id"],
            station_id=rel_id("station") or "",
            since=datetime.fromisoformat(a["since"]),
            till=datetime.fromisoformat(a["till"]),
            title=title,
            headline=(a.get("mirroredShow") or {}).get("title") or title,
            description=a.get("description") or "",
            has_show=bool(rel_id("show")),
            has_episode=bool(rel_id("episode")),
            has_audio=bool(a.get("audioLinks")),
        )

    @classmethod
    def from_flat(cls, d: dict) -> "ScheduleItem":
        """The flattened audit dump format (scratch schedule_*.json)."""
        title = d.get("title") or ""
        return cls(
            id=d["id"], station_id=d.get("station") or "",
            since=datetime.fromisoformat(d["since"]), till=datetime.fromisoformat(d["till"]),
            title=title, headline=d.get("show") or title, description=d.get("desc") or "",
            has_show=bool(d.get("show_id")), has_episode=bool(d.get("ep")),
            has_audio=bool(d.get("audio")),
        )

    def to_json(self) -> dict:
        return {"id": self.id, "since": self.since.isoformat(), "till": self.till.isoformat(),
                "headline": self.headline, "title": self.title, "description": self.description}


def pick_stream(audio_links: list[dict]) -> str | None:
    """Best direct stream for recording: AAC ≥128 kbps (160 'high'), else
    MP3; FLAC (cesnet, 32 'kbps' placeholder) and low-bitrate are skipped."""
    direct = [l for l in audio_links or [] if l.get("linkType") == "directstream" and l.get("url")]

    def rank(l: dict) -> tuple[int, int]:
        url = l["url"]
        kbps = int(l.get("bitrate") or 0)
        if url.endswith(".aac") and kbps >= 128:
            return (2, kbps)
        if url.endswith(".mp3") and kbps >= 96:
            return (1, kbps)
        return (0, kbps)

    best = max(direct, key=rank, default=None)
    return best["url"] if best and rank(best)[0] > 0 else None


def stream_urls(stations: list[dict]) -> dict[str, str]:
    """rAPI station UUID → recordable stream URL."""
    out = {}
    for s in stations:
        url = pick_stream((s.get("attributes") or {}).get("audioLinks") or [])
        if url:
            out[s["id"]] = url
    return out


class RapiClient:
    def __init__(self, sleep: Callable[[float], None] = time.sleep, gap_s: float = REQUEST_GAP_S,
                 retries: int = RETRIES):
        self._sleep = sleep
        self._gap = gap_s
        self._retries = retries

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                last = e
                wait = BACKOFF_429_S * (attempt + 1) if e.code == 429 else 5 * (attempt + 1)
                log.warning("live_rapi_http_error", url=url, status=e.code, wait_s=wait)
                self._sleep(wait)
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                last = e
                log.warning("live_rapi_error", url=url, error=str(e))
                self._sleep(5 * (attempt + 1))
        raise RuntimeError(f"rAPI request failed after {self._retries} attempts: {url}: {last}")

    def fetch_stations(self) -> list[dict]:
        return self._get(f"{API}/stations").get("data") or []

    def fetch_schedule(self, start: date, days: int) -> list[ScheduleItem]:
        items: list[ScheduleItem] = []
        for i in range(days):
            day = start + timedelta(days=i)
            items.extend(self._fetch_day(day))
        return items

    def _fetch_day(self, day: date) -> list[ScheduleItem]:
        nxt = day + timedelta(days=1)
        out: list[ScheduleItem] = []
        offset = 0
        while True:
            q = urllib.parse.urlencode({
                "filter[since][ge]": f"{day}T00:00:00{PRAGUE_OFFSET}",
                "filter[since][lt]": f"{nxt}T00:00:00{PRAGUE_OFFSET}",
                "page[limit]": PAGE_LIMIT, "page[offset]": offset})
            page = self._get(f"{API}/schedule?{q}")
            data = page.get("data") or []
            for e in data:
                try:
                    out.append(ScheduleItem.from_rapi(e))
                except (KeyError, ValueError):
                    log.warning("live_schedule_item_unparsable", id=e.get("id"))
            offset += len(data)
            self._sleep(self._gap)
            if not data or offset >= int((page.get("meta") or {}).get("count") or 0):
                return out
