"""Episode identity: one audio = one episode, whichever path saw it first.

Live case (2026-11-07, 'Dokumentarni povidky ...'): show-sweep stored parts
3-5 under the rAPI UUID, the crawler/gap-fill path stored them again under
the numeric yt-dlp id — same croaod stream, same part, same duration — and
both copies were downloaded into the same file.
"""
from __future__ import annotations

from audiobiblio.core.db.models import (
    Episode, EpisodeAlias, Program, Series, Station, Work,
)
from audiobiblio.dedupe.identity import find_same_audio

STREAM = "https://croaod.cz/stream/26638c4a.m4a/master.m3u8"
UUID = "26638c4a-35b2-3843-93ab-c0182c9a8d50"


def _work(db, program="Povidka", title="Dokumentarni povidky"):
    st = db.query(Station).filter_by(code="t").first()
    if st is None:
        st = Station(code="t", name="T")
        db.add(st)
        db.flush()
    p = db.query(Program).filter_by(name=program).first()
    if p is None:
        p = Program(station_id=st.id, name=program)
        db.add(p)
        db.flush()
    se = Series(program_id=p.id, name=f"{program} {title}")
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=title)
    db.add(w)
    db.flush()
    return w


def _ep(db, w, **kw):
    ep = Episode(work_id=w.id, title=kw.pop("title", "Dil"), **kw)
    db.add(ep)
    db.flush()
    return ep


def test_same_stream_url_matches_across_id_spaces(db_session):
    w = _work(db_session)
    ep = _ep(db_session, w, ext_id=UUID, url=STREAM, episode_number=3)
    hit, reason = find_same_audio(db_session, url=STREAM, ext_ids={"12152327"},
                                  work_id=w.id, episode_number=3,
                                  duration_ms=1_536_000)
    assert hit is not None and hit.id == ep.id
    assert reason == "media_url"


def test_stream_url_match_ignores_query_and_case_of_host(db_session):
    w = _work(db_session)
    ep = _ep(db_session, w, ext_id=UUID, url=STREAM, episode_number=3)
    hit, _ = find_same_audio(db_session, url=STREAM.replace("croaod.cz", "CROAOD.cz") + "?x=1",
                             ext_ids=set(), work_id=None, episode_number=None,
                             duration_ms=None)
    assert hit is not None and hit.id == ep.id


def test_page_url_is_not_an_audio_identity(db_session):
    """A program page lists many episodes — sharing it proves nothing."""
    w = _work(db_session)
    page = "https://www.mujrozhlas.cz/povidka"
    _ep(db_session, w, ext_id=UUID, url=page, episode_number=3)
    hit, _ = find_same_audio(db_session, url=page, ext_ids={"999"}, work_id=None,
                             episode_number=None, duration_ms=None)
    assert hit is None


def test_same_stream_but_different_part_numbers_is_not_a_match(db_session):
    w = _work(db_session)
    _ep(db_session, w, ext_id=UUID, url=STREAM, episode_number=4)
    hit, _ = find_same_audio(db_session, url=STREAM, ext_ids=set(), work_id=w.id,
                             episode_number=11, duration_ms=None)
    assert hit is None


def test_ext_id_matches_alias_ext_id(db_session):
    """The rAPI UUID row carries the numeric contentId as an alias."""
    w = _work(db_session)
    ep = _ep(db_session, w, ext_id=UUID, url=STREAM, episode_number=3)
    db_session.add(EpisodeAlias(episode_id=ep.id, url=STREAM, ext_id="12152327"))
    db_session.flush()
    hit, reason = find_same_audio(db_session, url="https://www.mujrozhlas.cz/x",
                                  ext_ids={"12152327"}, work_id=None,
                                  episode_number=None, duration_ms=None)
    assert hit is not None and hit.id == ep.id
    assert reason == "alias_ext_id"


def test_ext_id_matches_episode_ext_id(db_session):
    w = _work(db_session)
    ep = _ep(db_session, w, ext_id="12152327", url="https://x.cz/a")
    hit, reason = find_same_audio(db_session, url=None, ext_ids={UUID, "12152327"},
                                  work_id=None, episode_number=None, duration_ms=None)
    assert hit.id == ep.id and reason == "ext_id"


def test_same_part_same_duration_in_same_work_matches(db_session):
    w = _work(db_session)
    ep = _ep(db_session, w, ext_id=UUID, url="https://croaod.cz/stream/a.m3u8",
             episode_number=3, duration_ms=1_536_400)
    hit, reason = find_same_audio(db_session, url="https://croaod.cz/stream/b.m3u8",
                                  ext_ids={"12152327"}, work_id=w.id,
                                  episode_number=3, duration_ms=1_536_000)
    assert hit is not None and hit.id == ep.id
    assert reason == "part_duration"


def test_same_part_duration_off_by_more_than_a_second_is_not_a_match(db_session):
    w = _work(db_session)
    _ep(db_session, w, ext_id=UUID, url="https://croaod.cz/stream/a.m3u8",
        episode_number=3, duration_ms=1_536_000)
    hit, _ = find_same_audio(db_session, url="https://croaod.cz/stream/b.m3u8",
                             ext_ids={"1"}, work_id=w.id, episode_number=3,
                             duration_ms=1_537_500)
    assert hit is None


def test_part_duration_never_matches_in_catch_all_program(db_session):
    """The 'mujrozhlas' bucket holds many books sharing part numbers."""
    w = _work(db_session, program="mujrozhlas")
    _ep(db_session, w, ext_id=UUID, url="https://croaod.cz/stream/a.m3u8",
        episode_number=1, duration_ms=1_536_000)
    hit, _ = find_same_audio(db_session, url="https://croaod.cz/stream/b.m3u8",
                             ext_ids={"1"}, work_id=w.id, episode_number=1,
                             duration_ms=1_536_000)
    assert hit is None


def test_nothing_known_returns_none(db_session):
    hit, reason = find_same_audio(db_session, url=None, ext_ids=set(), work_id=None,
                                  episode_number=None, duration_ms=None)
    assert (hit, reason) == (None, None)
