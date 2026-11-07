from datetime import datetime

from audiobiblio.sources.rapi import playable_till


def test_earliest_playable_till_in_naive_utc():
    links = [{"playableTill": "2026-11-20T23:59:00+01:00"},
             {"playableTill": "2026-11-21T10:00:00+01:00"},
             {"url": "no-till"}]
    assert playable_till(links) == datetime(2026, 11, 20, 22, 59)


def test_naive_value_is_taken_as_utc():
    assert playable_till([{"playableTill": "2026-11-20T10:00:00"}]) == datetime(2026, 11, 20, 10, 0)


def test_missing_or_garbage_gives_none():
    assert playable_till(None) is None
    assert playable_till([]) is None
    assert playable_till([{"playableTill": "zítra"}]) is None
