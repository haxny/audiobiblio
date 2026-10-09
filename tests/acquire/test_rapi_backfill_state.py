"""Backfill cursor persistence."""
from __future__ import annotations

from audiobiblio.acquire import rapi_backfill_state as st


def test_roundtrip_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_path", lambda filename=None: tmp_path / "bf.json")
    assert st.load() == {}
    st.save({"a": 300})
    assert st.load() == {"a": 300}


def test_corrupt_file_means_no_backfill(tmp_path, monkeypatch):
    p = tmp_path / "bf.json"
    p.write_text("{not json")
    monkeypatch.setattr(st, "_path", lambda filename=None: p)
    assert st.load() == {}


def test_done_marker_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_path", lambda filename=None: tmp_path / "bf.json")
    st.save({"a": st.DONE})
    assert st.load() == {"a": st.DONE}
