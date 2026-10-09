"""metadata.json for Audiobookshelf — the narrator channel that does not
abuse the composer tag (ABS reads narrators from composer; user rule:
composer = author of music). ABS libraries rank absMetadata first."""
from __future__ import annotations

import json

from audiobiblio.core.db.models import FieldOrigin, MetadataValue
from audiobiblio.library.abs_metadata import (
    build_abs_metadata, split_names, write_abs_metadata,
)


def _mv(db, et, eid, field, value, origin=FieldOrigin.MANUAL):
    db.add(MetadataValue(entity_type=et, entity_id=eid, field=field, value=value,
                         origin=origin, source="t"))
    db.flush()


def test_split_names():
    assert split_names("Dana Syslova, Antonie Baresova a dalsi") == ["Dana Syslova", "Antonie Baresova"]
    assert split_names("Jiri Schwarz") == ["Jiri Schwarz"]
    assert split_names("Igor Bares a Petr Kubes") == ["Igor Bares", "Petr Kubes"]
    assert split_names("") == [] and split_names(None) == []


def test_build_from_resolved_values(db_session, episode_factory):
    ep = episode_factory()
    w = ep.work
    w.title, w.author, w.year = "Stare povesti ceske", "Alois Jirasek", 1894
    _mv(db_session, "work", w.id, "title", "Stare povesti ceske")
    _mv(db_session, "work", w.id, "publisher", "CRo3 2009")
    _mv(db_session, "episode", ep.id, "narrator", "Jiri Schwarz")
    _mv(db_session, "episode", ep.id, "genre", "audiokniha; povesti; Ctenarsky denik (CRo3)")
    _mv(db_session, "episode", ep.id, "description", "Jak to bylo")
    md = build_abs_metadata(db_session, w)
    assert md["title"] == "Stare povesti ceske"
    assert md["authors"] == ["Alois Jirasek"]
    assert md["narrators"] == ["Jiri Schwarz"]
    assert md["genres"] == ["audiokniha", "povesti", "Ctenarsky denik (CRo3)"]
    assert md["publishedYear"] == "1894"
    assert md["publisher"] == "CRo3 2009"
    assert md["description"] == "Jak to bylo"


def test_write_merges_and_keeps_foreign_keys(tmp_path):
    f = tmp_path / "metadata.json"
    f.write_text(json.dumps({"title": "Old", "chapters": [{"id": 0, "start": 0, "end": 5, "title": "x"}],
                             "narrators": ["Wrong"], "tags": ["keep"]}))
    changed = write_abs_metadata(tmp_path, {"title": "New", "narrators": ["Jiri Schwarz"], "authors": []})
    data = json.loads(f.read_text())
    assert changed
    assert data["title"] == "New" and data["narrators"] == ["Jiri Schwarz"]
    assert data["chapters"][0]["title"] == "x" and data["tags"] == ["keep"]
    assert "authors" not in data          # empty values never overwrite


def test_write_is_noop_when_unchanged(tmp_path):
    write_abs_metadata(tmp_path, {"title": "A"})
    assert write_abs_metadata(tmp_path, {"title": "A"}) is False


def test_write_tolerates_corrupt_existing_file(tmp_path):
    (tmp_path / "metadata.json").write_text("{not json")
    assert write_abs_metadata(tmp_path, {"title": "A"})
    assert json.loads((tmp_path / "metadata.json").read_text())["title"] == "A"


def test_radio_original_uses_recording_year_not_broadcast(db_session, episode_factory):
    ep = episode_factory(); w = ep.work
    w.title, w.author, w.year = "Krvava pavlac", "Roman Ludva", None
    _mv(db_session, "work", w.id, "publisher", "CRoOl 2016")
    from datetime import datetime
    ep.published_at = datetime(2026, 6, 1)
    assert build_abs_metadata(db_session, w)["publishedYear"] == "2016"


def test_tag_year_of_radio_original_is_recording_year(db_session, episode_factory):
    from datetime import datetime
    from audiobiblio.core.provenance import resolve_field
    from audiobiblio.library.sync import _get_candidates, compute_resolved
    ep = episode_factory(); w = ep.work
    w.year = None
    ep.published_at = datetime(2026, 6, 1)
    _mv(db_session, "work", w.id, "publisher", "CRoOl 2016")            # MANUAL
    _mv(db_session, "work", w.id, "year", "2026", FieldOrigin.FILE)     # broadcast year seen in file
    assert compute_resolved(db_session, ep)["year"] == "2016"
    win = resolve_field(_get_candidates(db_session, "work", w.id, "year"))
    assert win.origin == FieldOrigin.MANUAL                              # passes the shelf guard
    w.year = 1951                                                        # a book's first edition wins
    assert all(c.source != "derived:publisher" for c in _get_candidates(db_session, "work", w.id, "year"))
