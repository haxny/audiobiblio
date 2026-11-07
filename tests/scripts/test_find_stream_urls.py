"""scripts/find_stream_urls.py — read-only listing of episodes whose url is a stream."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from audiobiblio.core.db.models import Episode, EpisodeAlias, Program, Series, Station, Work

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "find_stream_urls.py"


def _load():
    spec = importlib.util.spec_from_file_location("find_stream_urls", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module
    spec.loader.exec_module(mod)
    return mod


def test_lists_stream_url_episodes_with_page_alias(db_session):
    st = Station(code="c", name="C")
    db_session.add(st)
    db_session.flush()
    p = Program(station_id=st.id, name="P")
    db_session.add(p)
    db_session.flush()
    se = Series(program_id=p.id, name="P")
    db_session.add(se)
    db_session.flush()
    w = Work(series_id=se.id, title="W")
    db_session.add(w)
    db_session.flush()
    eps = [Episode(work_id=w.id, title="a", discovery_source="show-sweep",
                   url="https://croaod.cz/stream/u1.m4a/playlist.m3u8"),
           Episode(work_id=w.id, title="b", url="https://example.cz/x/playlist.m3u8"),
           Episode(work_id=w.id, title="c", url="https://www.mujrozhlas.cz/povidka/c")]
    db_session.add_all(eps)
    db_session.flush()
    db_session.add(EpisodeAlias(episode_id=eps[0].id, url="https://vltava.rozhlas.cz/a-1234567"))
    db_session.flush()

    rows = _load().find_stream_episodes(db_session)
    by_id = {r.episode_id: r for r in rows}
    assert set(by_id) == {eps[0].id, eps[1].id}
    assert by_id[eps[0].id].page_alias == "https://vltava.rozhlas.cz/a-1234567"
    assert by_id[eps[0].id].discovery_source == "show-sweep"
    assert by_id[eps[1].id].page_alias is None
