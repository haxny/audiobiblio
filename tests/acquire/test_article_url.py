"""Episode url must be the episode page, never the audio stream (bug: show-sweep
stored 'croaod.cz/stream/…/playlist.m3u8' as Episode.url and the www tag
became '<stream>;<program page>')."""
from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy import text

from audiobiblio.acquire import serial_sweep
from audiobiblio.acquire.downloader import _audio_source_url
from audiobiblio.core.db.models import (
    Episode, EpisodeAlias, Program, Series, Station, Work,
)
from audiobiblio.core.urls import is_media_url
from audiobiblio.library.pipelines.postprocess import _build_www

STREAM = "https://croaod.cz/stream/48891de5-0000-0000-0000-000000000001.m4a/playlist.m3u8"
UUID = "196c7a26-8311-3fcd-9e18-c1a3f32da879"
ARTICLE = "https://vltava.rozhlas.cz/dokumentarni-povidky-9652973"
PROGRAM_URL = "https://vltava.rozhlas.cz/povidka-5057586"


def _program(db, name="Povidka", url=PROGRAM_URL):
    st = Station(code="cro3", name="Vltava")
    db.add(st)
    db.flush()
    p = Program(station_id=st.id, name=name, url=url)
    db.add(p)
    db.flush()
    se = Series(program_id=p.id, name=name)
    db.add(se)
    db.flush()
    return p, se


def _resp(payload):
    class _Resp:
        code = 301
        headers = {"Location": "https://www.mujrozhlas.cz/rapi/view/show/"
                               "0038fee4-b218-3a73-a4a4-04544f431aad"}

        def read(self):
            return json.dumps(payload).encode()
    return _Resp()


def _payload(part=2, title="Milos Dolezal: Bohous svine"):
    return {"data": [{"id": UUID, "attributes": {
        "title": title, "part": part,
        "audioLinks": [{"variant": "hls", "duration": 100, "url": STREAM}]}}]}


def test_show_sweep_stores_episode_page_not_stream(db_session):
    _program(db_session)
    db_session.execute(text(
        "INSERT INTO crawl_targets (url, kind, name, active, approval_mode, "
        "interval_hours, created_at) VALUES (:u, 'PROGRAM', 'Povidka', 1, 'AUTO', "
        "24, datetime('now'))"), {"u": PROGRAM_URL})
    with patch.object(serial_sweep, "_http", return_value=_resp(_payload())), \
         patch.object(serial_sweep.time, "sleep"):
        serial_sweep.run_show_sweep(db_session)

    ep = db_session.query(Episode).filter_by(ext_id=UUID).one()
    assert not is_media_url(ep.url)
    assert ep.url == f"https://www.mujrozhlas.cz/rapi/view/episode/{UUID}"
    # the stream is kept (as an alias) for the downloader
    aliases = [a.url for a in db_session.query(EpisodeAlias).filter_by(episode_id=ep.id)]
    assert STREAM in aliases
    db_session.refresh(ep)
    assert _audio_source_url(ep) == STREAM


def test_serial_sweep_stores_episode_page_not_stream(db_session):
    _, se = _program(db_session, name="Cetba")
    w = Work(series_id=se.id, title="Kniha")
    db_session.add(w)
    db_session.flush()
    with patch.object(serial_sweep, "_http", return_value=_resp(_payload(1, "1. dil"))):
        assert serial_sweep._expand_serial(db_session, "s", w.id) == 1
    ep = db_session.query(Episode).filter_by(ext_id=UUID).one()
    assert not is_media_url(ep.url)
    db_session.refresh(ep)
    assert _audio_source_url(ep) == STREAM


class TestAudioSourceUrl:
    def test_media_url_is_used_directly(self):
        assert _audio_source_url(Episode(url=STREAM, title="x")) == STREAM

    def test_page_without_stream_alias_downloads_the_page(self):
        ep = Episode(url="https://www.mujrozhlas.cz/povidka/x", title="x")
        assert _audio_source_url(ep) == "https://www.mujrozhlas.cz/povidka/x"

    def test_newest_stream_alias_wins(self):
        ep = Episode(url="https://www.mujrozhlas.cz/povidka/x", title="x")
        ep.aliases = [EpisodeAlias(id=1, url="https://croaod.cz/stream/old.m4a/playlist.m3u8"),
                      EpisodeAlias(id=2, url="https://vltava.rozhlas.cz/x-1234567"),
                      EpisodeAlias(id=3, url=STREAM)]
        assert _audio_source_url(ep) == STREAM


class TestWwwTag:
    def _prog(self):
        return Program(name="Povidka", url=PROGRAM_URL)

    def test_article_only_never_program_page(self):
        ep = Episode(url=ARTICLE, title="x")
        assert _build_www(ep, self._prog()) == ARTICLE

    def test_stream_url_replaced_by_page_alias(self):
        ep = Episode(url=STREAM, title="x")
        ep.aliases = [EpisodeAlias(id=1, url=ARTICLE)]
        assert _build_www(ep, self._prog()) == ARTICLE

    def test_stream_only_writes_nothing(self):
        assert _build_www(Episode(url=STREAM, title="x"), self._prog()) == ""
