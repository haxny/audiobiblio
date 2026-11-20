"""Independent check: does a book hold every part rAPI lists for its serial?

Live 2026-11 (Dolezal serial): part 2 never reached the book and nothing noticed
until the user did — every fix so far was on one ingest path and nothing
verified the RESULT. This verifier compares the end state with the source,
whatever path built the book.
"""
from __future__ import annotations

import urllib.error

from audiobiblio.acquire import serial_verify
from audiobiblio.core.db.models import (
    Episode, EpisodeAlias, Program, Series, Station, Work,
)

SERIAL = "77bd5bc8-7870-395d-a8c1-753f6e51bc69"


def _uuid(n: int) -> str:
    return f"00000000-0000-3000-8000-{n:012d}"


def _book(db, title="Doležal", serial=SERIAL, parts=(1, 3, 4, 5), program="P", base=0):
    st = db.query(Station).first() or Station(code="x", name="X")
    db.add(st)
    db.flush()
    p = db.query(Program).filter_by(name=program).first() or Program(station_id=st.id, name=program)
    db.add(p)
    db.flush()
    se = db.query(Series).filter_by(program_id=p.id, name=program).first() or Series(program_id=p.id, name=program)
    db.add(se)
    db.flush()
    w = Work(series_id=se.id, title=title, serial_uuid=serial)
    db.add(w)
    db.flush()
    for n in parts:
        db.add(Episode(work_id=w.id, ext_id=_uuid(base + n), title=f"d{n}", episode_number=n))
    db.commit()
    return w


def _rapi_ep(n, live=True, cid=None):
    ep = {"id": _uuid(n),
          "attributes": {"part": n, "title": f"Díl {n}",
                         "mirroredSerial": {"title": "Doležal", "totalParts": 5},
                         "audioLinks": [{"variant": "hls", "url": f"https://croaod.cz/s{n}.m3u8"}] if live else []}}
    if cid:
        ep["meta"] = {"ga": {"contentId": int(cid)}}
    return ep


def _fetch(episodes, serial=SERIAL):
    seen = []

    def fetch(url):
        seen.append(url)
        if "/serials/" in url:
            return {"data": episodes}
        return {"data": {"relationships": {"serial": {"data": {"id": serial}}}}}
    fetch.seen = seen
    return fetch


def test_missing_live_part_is_reported(db_session):
    w = _book(db_session)
    chk = serial_verify.verify_book(db_session, w, _fetch([_rapi_ep(n) for n in range(1, 6)]))
    assert chk.missing_live == (2,) and chk.missing_expired == ()
    assert chk.have == (1, 3, 4, 5) and chk.rapi_total == 5 and chk.complete is False


def test_expired_part_is_not_a_download_candidate(db_session):
    w = _book(db_session)
    eps = [_rapi_ep(n, live=(n != 2)) for n in range(1, 6)]
    chk = serial_verify.verify_book(db_session, w, _fetch(eps))
    assert chk.missing_live == () and chk.missing_expired == (2,)


def test_complete_book_has_no_finding(db_session):
    w = _book(db_session, parts=(1, 2, 3, 4, 5))
    chk = serial_verify.verify_book(db_session, w, _fetch([_rapi_ep(n) for n in range(1, 6)]))
    assert chk.complete and chk.missing_live == ()


def test_part_known_by_alias_id_counts_as_present(db_session):
    """The crawler's numeric contentId may be the only key the book holds."""
    w = _book(db_session, parts=(1, 3, 4, 5))
    ep = Episode(work_id=w.id, ext_id="999", title="x", episode_number=None)
    db_session.add(ep)
    db_session.flush()
    db_session.add(EpisodeAlias(episode_id=ep.id, ext_id=_uuid(2), discovery_source="t"))
    db_session.commit()
    chk = serial_verify.verify_book(db_session, w, _fetch([_rapi_ep(n) for n in range(1, 6)]))
    assert chk.missing_live == ()


def test_part_owned_by_another_book_is_still_missing_here(db_session):
    """Own book, own episode: a recording held elsewhere does not complete it."""
    w = _book(db_session)
    other = _book(db_session, title="Jiný cyklus", serial=None, parts=(), program="Q")
    db_session.add(Episode(work_id=other.id, ext_id=_uuid(2), title="d2", episode_number=1))
    db_session.commit()
    chk = serial_verify.verify_book(db_session, w, _fetch([_rapi_ep(n) for n in range(1, 6)]))
    assert chk.missing_live == (2,) and chk.held_elsewhere == (2,)


def test_serial_is_discovered_from_a_uuid_episode_when_not_stamped(db_session):
    w = _book(db_session, serial=None)
    fetch = _fetch([_rapi_ep(n) for n in range(1, 6)])
    chk = serial_verify.verify_book(db_session, w, fetch)
    assert chk.serial_uuid == SERIAL and chk.discovered is True
    assert db_session.get(Work, w.id).serial_uuid is None      # report-only: nothing stamped


def test_book_without_any_uuid_episode_is_skipped(db_session):
    w = _book(db_session, serial=None, parts=())
    db_session.add(Episode(work_id=w.id, ext_id="12345", title="legacy", episode_number=1))
    db_session.commit()
    assert serial_verify.verify_book(db_session, w, _fetch([])) is None


def test_verify_all_respects_the_limit_and_reports_only_incomplete(db_session):
    _book(db_session, title="A", serial=SERIAL)
    _book(db_session, title="B", serial="11111111-1111-3111-8111-111111111111", parts=(1, 2, 3, 4, 5), program="Q", base=100)

    def fetch(url):
        if "11111111-1111" in url:
            return {"data": [_rapi_ep(n) for n in range(1, 6)]}
        return {"data": [_rapi_ep(n) for n in range(1, 6)]}
    rep = serial_verify.verify_all(db_session, fetch=fetch, limit=10, sleep=lambda s: None)
    assert rep.checked == 2 and len(rep.incomplete) == 1 and rep.incomplete[0].title == "A"
    one = serial_verify.verify_all(db_session, fetch=fetch, limit=1, sleep=lambda s: None)
    assert one.checked == 1


def test_a_failing_request_is_reported_not_raised(db_session):
    w = _book(db_session)

    def boom(url):
        raise OSError("network down")
    chk = serial_verify.verify_book(db_session, w, boom)
    assert chk.error and "network down" in chk.error and chk.complete is False


def _http_404():
    return urllib.error.HTTPError("https://api", 404, "Not Found", {}, None)


def test_unknown_episode_during_discovery_is_skipped_not_an_error(db_session):
    """Live 2026-11-20: 183 single news episodes answered 404 on /episodes/<id>;
    they are not serial books and must not flood the report with errors."""
    w = _book(db_session, serial=None)

    def fetch(url):
        raise _http_404()
    assert serial_verify.verify_book(db_session, w, fetch) is None


def test_stamped_serial_that_is_gone_is_a_finding(db_session):
    w = _book(db_session)

    def fetch(url):
        raise _http_404()
    chk = serial_verify.verify_book(db_session, w, fetch)
    assert chk is not None and chk.error and "404" in chk.error


def test_report_names_the_books_that_hold_the_missing_parts(db_session):
    """The split-book signature: parts 2.. live in another work (live: Nabokov
    Lolita, Chramostova, Viewegh ... after 2026-11-20)."""
    w = _book(db_session, parts=(1,), serial=None)
    other = _book(db_session, title="Druhá půlka", serial=None, parts=(2, 3, 4, 5), program="Q", base=0)
    chk = serial_verify.verify_book(db_session, w, _fetch([_rapi_ep(n) for n in range(1, 6)]))
    assert chk.held_in == (other.id,)


def test_verify_all_prefers_multi_part_books_over_single_episodes(db_session):
    """Single news episodes (one UUID episode each) are not worth a request."""
    for i in range(5):
        _book(db_session, title=f"Zpráva {i}", serial=None, parts=(1,), program=f"N{i}", base=1000 + 10 * i)
    multi = _book(db_session, title="Kniha", serial=None, parts=(1, 2), program="B", base=2000)
    seen = []

    def fetch(url):
        seen.append(url)
        return {"data": [_rapi_ep(n) for n in range(1, 3)]} if "/serials/" in url else \
               {"data": {"relationships": {"serial": {"data": {"id": SERIAL}}}}}
    rep = serial_verify.verify_all(db_session, fetch=fetch, limit=10, sleep=lambda s: None)
    assert rep.checked == 1 and rep.skipped == 0
    assert any(str(2000 + 1) in u or "0002001" in u for u in seen)
