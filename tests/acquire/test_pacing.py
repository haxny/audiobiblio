"""Human-like pacing rules: night window, daytime hourly cap."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import audiobiblio.acquire.downloader as dl


def test_night_window_boundaries(monkeypatch):
    tz = ZoneInfo("Europe/Prague")
    assert dl._is_night(datetime(2026, 7, 24, 19, 0, tzinfo=tz))
    assert dl._is_night(datetime(2026, 7, 24, 23, 30, tzinfo=tz))
    assert dl._is_night(datetime(2026, 7, 24, 4, 59, tzinfo=tz))
    assert not dl._is_night(datetime(2026, 7, 24, 5, 0, tzinfo=tz))
    assert not dl._is_night(datetime(2026, 7, 24, 12, 0, tzinfo=tz))
    assert not dl._is_night(datetime(2026, 7, 24, 18, 59, tzinfo=tz))


def test_day_quota(monkeypatch):
    monkeypatch.setattr(dl, "_is_night", lambda now=None: False)
    dl._audio_done_at.clear()
    now = time.time()
    for _ in range(dl.DAY_HOURLY_AUDIO_CAP):
        dl._audio_done_at.append(now)
    assert dl._day_quota_exhausted(now)
    # old timestamps age out of the rolling hour
    dl._audio_done_at.clear()
    for _ in range(dl.DAY_HOURLY_AUDIO_CAP):
        dl._audio_done_at.append(now - 3700)
    assert not dl._day_quota_exhausted(now)


def test_night_unlimited(monkeypatch):
    monkeypatch.setattr(dl, "_is_night", lambda now=None: True)
    dl._audio_done_at.clear()
    for _ in range(200):
        dl._audio_done_at.append(time.time())
    assert not dl._day_quota_exhausted()
