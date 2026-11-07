"""Health detectors: read-only functions over the DB, one list of findings
each (severity, one-line Czech message, plain link)."""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timedelta

import pytest

from audiobiblio.core.db.models import (
    ApprovalMode, Asset, AssetStatus, AssetType, CrawlRun, CrawlTarget, CrawlTargetKind,
    DownloadJob, Episode, JobStatus, Program, Series, Station, Work,
)
from audiobiblio.library.health import detectors as d
from audiobiblio.library.health.findings import error_signature

NOW = datetime(2026, 11, 7, 6, 30)
BASE = "http://nasx:8321"


@pytest.fixture(autouse=True)
def _base_url(monkeypatch):
    monkeypatch.setenv("AUDIOBIBLIO_PUBLIC_URL", BASE)


class World:
    def __init__(self, db):
        self.db = db
        self.st = Station(code="cro2", name="Dvojka")
        db.add(self.st)
        db.flush()
        self.n = 0

    def program(self, name, url=None):
        p = Program(station_id=self.st.id, name=name, url=url)
        self.db.add(p)
        self.db.flush()
        se = Series(program_id=p.id, name=name)
        self.db.add(se)
        self.db.flush()
        p._series = se
        return p

    def work(self, program, title="Kniha", total=None):
        w = Work(series_id=program._series.id, title=title, expected_total=total)
        self.db.add(w)
        self.db.flush()
        return w

    def episode(self, work, created=NOW - timedelta(days=1), **kw):
        self.n += 1
        ep = Episode(work_id=work.id, title=kw.pop("title", f"díl {self.n}"),
                     ext_id=f"e{self.n}", created_at=created, **kw)
        self.db.add(ep)
        self.db.flush()
        return ep

    def audio(self, ep, status=AssetStatus.COMPLETE, path=None):
        a = Asset(episode_id=ep.id, type=AssetType.AUDIO, status=status,
                  file_path=path or f"/media/a/{ep.id}.m4a")
        self.db.add(a)
        self.db.flush()
        return a

    def target(self, url, name, mode=ApprovalMode.AUTO, created=NOW - timedelta(days=30),
               last=NOW - timedelta(hours=3), active=True):
        t = CrawlTarget(url=url, kind=CrawlTargetKind.PROGRAM, name=name, active=active,
                        approval_mode=mode, interval_hours=24, created_at=created,
                        last_crawled_at=last)
        self.db.add(t)
        self.db.flush()
        return t

    def run(self, target, ago_days, new=0, error=None, path="crawl"):
        r = CrawlRun(target_id=target.id, target_url=target.url, path=path,
                     started_at=NOW - timedelta(days=ago_days),
                     finished_at=NOW - timedelta(days=ago_days), new_episodes=new,
                     jobs_queued=0, error=error)
        self.db.add(r)
        self.db.flush()
        return r


@pytest.fixture()
def w(db_session):
    return World(db_session)


# --- (a) silent targets ------------------------------------------------------

def test_auto_target_with_zero_episodes_after_three_crawls(w):
    w.program("Sobotní drama", url="https://www.mujrozhlas.cz/sobotni-drama")
    t = w.target("https://www.mujrozhlas.cz/sobotni-drama", "Sobotni drama")
    f = d.detect_silent_targets(w.db, now=NOW)
    assert [x.key for x in f] == [f"target:{t.id}"]
    assert f[0].severity == "warn"
    assert "0 epizod" in f[0].message
    assert f[0].link.startswith(f"{BASE}/programs/")


def test_target_crawled_too_few_times_is_not_flagged(w):
    w.target("https://www.mujrozhlas.cz/novy", "Nový pořad", created=NOW - timedelta(days=1))
    assert d.detect_silent_targets(w.db, now=NOW) == []


def test_never_crawled_target_is_not_flagged(w):
    w.target("https://www.mujrozhlas.cz/x", "X", last=None)
    assert d.detect_silent_targets(w.db, now=NOW) == []


def test_unmapped_target_links_its_source_url(w):
    t = w.target("https://www.mujrozhlas.cz/hry-a-cetba/kategorie", "Hry a četba")
    f = d.detect_silent_targets(w.db, now=NOW)
    assert f[0].key == f"target:{t.id}"
    assert f[0].link == t.url


def test_review_target_flagged_only_when_book_named(w):
    w.target("https://radiozurnal.rozhlas.cz/vecerni-radiozurnal-1", "Večerní Radiožurnál",
             mode=ApprovalMode.REVIEW)
    book = w.target("https://hradec.rozhlas.cz/divadlo-pro-vase-usi-6193592",
                    "Divadlo pro vaše uši", mode=ApprovalMode.REVIEW)
    f = d.detect_silent_targets(w.db, now=NOW)
    assert [x.key for x in f] == [f"target:{book.id}"]
    assert f[0].severity == "info"


def test_recorded_runs_count_as_crawls(w):
    t = w.target("https://www.mujrozhlas.cz/cetba-x", "Četba X", created=NOW - timedelta(days=1))
    for i in range(3):
        w.run(t, ago_days=i * 0.1)
    assert [x.key for x in d.detect_silent_targets(w.db, now=NOW)] == [f"target:{t.id}"]


def test_auto_target_stalled_for_a_month(w):
    p = w.program("Hra na neděli", url="https://dvojka.rozhlas.cz/hra-na-nedeli-7785729")
    w.episode(w.work(p), created=NOW - timedelta(days=45))
    t = w.target(p.url, "Hra na neděli (CRo2)")
    f = d.detect_silent_targets(w.db, now=NOW)
    assert [x.key for x in f] == [f"target:{t.id}"]
    assert "45 dní" in f[0].message


def test_stalled_target_with_recent_new_run_is_not_flagged(w):
    p = w.program("Hra na neděli", url="https://dvojka.rozhlas.cz/hra-na-nedeli-7785729")
    w.episode(w.work(p), created=NOW - timedelta(days=45))
    t = w.target(p.url, "Hra na neděli (CRo2)")
    w.run(t, ago_days=1, new=2)
    assert d.detect_silent_targets(w.db, now=NOW) == []


def test_active_auto_target_with_fresh_episodes_is_fine(w):
    p = w.program("Četba na pokračování", url="https://dvojka.rozhlas.cz/cetba-7230785")
    w.episode(w.work(p), created=NOW - timedelta(days=2))
    w.target(p.url, "Cetba na pokracovani (CRo2) — stanice")
    assert d.detect_silent_targets(w.db, now=NOW) == []


def test_target_maps_to_program_by_unaccented_name(w):
    p = w.program("Četba na pokračování")
    w.episode(w.work(p), created=NOW - timedelta(days=2))
    w.target("https://dvojka.rozhlas.cz/cetba-na-pokracovani-7230785",
             "Cetba na pokracovani (CRo2) — stanice")
    assert d.detect_silent_targets(w.db, now=NOW) == []


def test_inactive_targets_are_ignored(w):
    w.target("https://www.mujrozhlas.cz/x", "Četba", active=False)
    assert d.detect_silent_targets(w.db, now=NOW) == []


# --- (b) failing targets -------------------------------------------------------

def test_target_failing_the_same_way_three_times(w):
    t = w.target("https://brno.rozhlas.cz/x-123", "X", mode=ApprovalMode.REVIEW)
    for i in range(3):
        w.run(t, ago_days=i, error=f"yt-dlp probe timed out after 180s (pid {i}23)")
    f = d.detect_failing_targets(w.db, now=NOW)
    assert [x.key for x in f] == [f"target:{t.id}:crawl"]
    assert "3×" in f[0].message and "timed out" in f[0].message
    assert f[0].group == error_signature("yt-dlp probe timed out after 180s (pid 1)")


def test_failing_path_is_not_hidden_by_a_healthy_path(w):
    """Daily show sweep fails, hourly crawl of the same target succeeds:
    the runs interleave, yet the sweep's streak must be reported."""
    t = w.target("https://dvojka.rozhlas.cz/x-1234567", "X")
    for i in range(3):
        w.run(t, ago_days=i, path="show-sweep", error="show-redirect: no show UUID (HTTP 404)")
        w.run(t, ago_days=i + 0.5, path="crawl")
    f = d.detect_failing_targets(w.db, now=NOW)
    assert [x.key for x in f] == [f"target:{t.id}:show-sweep"]


def test_recovered_target_is_not_failing(w):
    t = w.target("https://brno.rozhlas.cz/x-123", "X")
    w.run(t, ago_days=2, error="HTTP Error 404")
    w.run(t, ago_days=1, error="HTTP Error 404")
    w.run(t, ago_days=0)
    assert d.detect_failing_targets(w.db, now=NOW) == []


def test_different_errors_are_not_a_pattern(w):
    t = w.target("https://brno.rozhlas.cz/x-123", "X")
    w.run(t, ago_days=2, error="HTTP Error 404")
    w.run(t, ago_days=1, error="database is locked")
    w.run(t, ago_days=0, error="HTTP Error 404")
    assert d.detect_failing_targets(w.db, now=NOW) == []


def test_error_signature_collapses_ids_and_commands():
    a = error_signature("\nCommand '['/usr/local/bin/yt-dlp', '--match-filter', 'id=8171393-4', "
                        "'https://vltava.rozhlas.cz/node/8171393']' returned non-zero exit status 1.")
    b = error_signature("\nCommand '['/usr/local/bin/yt-dlp', '--match-filter', 'id=hry-a-cetba-2', "
                        "'https://temata.rozhlas.cz/hry-a-cetba']' returned non-zero exit status 1.")
    assert a == b == "yt-dlp: exit 1"
    assert error_signature("HTTPSConnectionPool(host='ostrava.rozhlas.cz', port=443): "
                           "Read timed out. (read timeout=30)") == error_signature(
        "HTTPSConnectionPool(host='brno.rozhlas.cz', port=443): Read timed out. (read timeout=30)")
    assert len(error_signature("x" * 500)) <= 100


# --- (c) expiring --------------------------------------------------------------

def test_expiring_not_downloaded_book_episode(w):
    p = w.program("Četba na pokračování")
    work = w.work(p, title="Baarová")
    ep = w.episode(work, playable_till=NOW + timedelta(days=4))
    done = w.episode(work, playable_till=NOW + timedelta(days=4))
    w.audio(done)
    w.episode(work, playable_till=NOW + timedelta(days=30))   # far away
    w.episode(work, playable_till=NOW - timedelta(days=1))    # already gone
    w.db.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                         status=JobStatus.APPROVAL))
    w.db.flush()
    f = d.detect_expiring(w.db, now=NOW)
    assert [x.key for x in f] == [f"episode:{ep.id}"]
    assert f[0].severity == "warn"
    assert "4 dn" in f[0].message and "APPROVAL" in f[0].message
    assert f[0].link == f"{BASE}/works/{work.id}"


def test_expiring_one_shot_title_is_not_repeated(w):
    work = w.work(w.program("Povídka"), title="Veliký diamant")
    w.episode(work, title="Veliký diamant", playable_till=NOW + timedelta(days=6))
    assert d.detect_expiring(w.db, now=NOW)[0].message.startswith("Veliký diamant: vyprší")


def test_expiring_within_three_days_is_critical(w):
    work = w.work(w.program("Povídka"))
    w.episode(work, playable_till=NOW + timedelta(days=2))
    assert d.detect_expiring(w.db, now=NOW)[0].severity == "crit"


def test_expiring_news_episode_is_ignored(w):
    work = w.work(w.program("Hlavní zprávy"), title="Zprávy")
    w.episode(work, playable_till=NOW + timedelta(days=2))
    assert d.detect_expiring(w.db, now=NOW) == []


def test_expiring_serial_part_counts_even_in_unknown_program(w):
    work = w.work(w.program("Odpolední Vltava"), title="Román", total=8)
    w.episode(work, playable_till=NOW + timedelta(days=5))
    assert len(d.detect_expiring(w.db, now=NOW)) == 1


def test_expiring_on_auto_target_program_counts(w):
    p = w.program("Esej", url="https://vltava.rozhlas.cz/esej-5057469")
    w.target(p.url, "Esej")
    w.episode(w.work(p), playable_till=NOW + timedelta(days=5))
    assert len(d.detect_expiring(w.db, now=NOW)) == 1


# --- (d) librarian -------------------------------------------------------------

def test_librarian_stuck_counts_and_age(w):
    p = w.program("Četba na pokračování")
    old = w.work(p, title="Stará")
    new = w.work(p, title="Nová")
    ok = w.work(p, title="Hotová")
    for work, days in ((old, 60), (new, 2), (ok, 20)):
        a = w.audio(w.episode(work))
        a.first_seen_at = NOW - timedelta(days=days)
    w.db.flush()
    verdicts = [
        {"kind": "WAITING-METADATA", "work_id": old.id, "detail": "chybí autor"},
        {"kind": "AUTHOR-CHECK", "work_id": new.id, "detail": "autor?"},
        {"kind": "SHELVE", "work_id": ok.id, "detail": ""},
    ]
    f = d.detect_librarian_stuck(w.db, now=NOW, verdicts=verdicts)
    by_key = {x.key: x for x in f}
    assert set(by_key) == {f"work:{old.id}", f"work:{new.id}"}
    assert by_key[f"work:{old.id}"].severity == "warn"     # waiting > 30 days
    assert by_key[f"work:{new.id}"].severity == "info"
    assert by_key[f"work:{old.id}"].group == "WAITING-METADATA"
    assert "60 dní" in by_key[f"work:{old.id}"].message
    assert f[0].key == f"work:{old.id}"                    # oldest first
    assert by_key[f"work:{new.id}"].link == f"{BASE}/works/{new.id}"


# --- (e) retry cap ---------------------------------------------------------------

def test_retry_cap_errors_in_last_day_grouped_by_error(w):
    work = w.work(w.program("P"))
    capped, old_cap, two = w.episode(work), w.episode(work), w.episode(work)
    def err(ep, n, when, text="HTTP Error 403: Forbidden"):
        for _ in range(n):
            w.db.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                                 status=JobStatus.ERROR, error=text, finished_at=when))
    err(capped, 3, NOW - timedelta(hours=2))
    err(old_cap, 3, NOW - timedelta(days=3))      # capped long ago — not new
    err(two, 2, NOW - timedelta(hours=1))         # below the cap
    w.db.flush()
    f = d.detect_retry_cap_errors(w.db, now=NOW)
    assert [x.key for x in f] == [f"job:{capped.id}:AUDIO"]
    assert f[0].group == error_signature("HTTP Error 403: Forbidden")
    assert f[0].link == f"{BASE}/episodes/{capped.id}"


# --- (f) disk ----------------------------------------------------------------------

Usage = namedtuple("Usage", "total used free")


def test_disk_space_thresholds():
    ok = d.detect_disk_space(["/m"], usage_fn=lambda p: Usage(100, 80, 20))
    warn = d.detect_disk_space(["/m"], usage_fn=lambda p: Usage(100, 92, 8))
    crit = d.detect_disk_space(["/m"], usage_fn=lambda p: Usage(100, 96, 4))
    assert ok == []
    assert warn[0].severity == "warn" and "8 %" in warn[0].message
    assert crit[0].severity == "crit"
    assert warn[0].key != crit[0].key  # escalation is a new finding


def test_disk_space_missing_path_is_reported():
    def boom(p):
        raise FileNotFoundError(p)
    f = d.detect_disk_space(["/nope"], usage_fn=boom)
    assert f[0].severity == "warn" and "/nope" in f[0].message


# --- (g) shared files ---------------------------------------------------------------

def test_episodes_sharing_one_audio_file(w):
    p = w.program("Oldies")
    work = w.work(p, title="Oldies jako na dlani")
    a, b, c = w.episode(work), w.episode(work), w.episode(work)
    w.audio(a, path="/media/x/Oldies - 01.m4a")
    w.audio(b, path="/media/x/Oldies - 01.m4a")
    w.audio(c, path="/media/x/Oldies - 02.m4a")
    f = d.detect_shared_files(w.db, now=NOW)
    assert [x.key for x in f] == ["path:/media/x/Oldies - 01.m4a"]
    assert "2 epizody" in f[0].message
    assert f[0].link == f"{BASE}/works/{work.id}"


# --- registry ----------------------------------------------------------------------

def test_registry_lists_all_seven_detectors():
    ids = [x.id for x in d.DETECTORS]
    assert ids == ["silent_targets", "failing_targets", "expiring", "librarian",
                   "retry_cap", "disk", "shared_files"]
    assert all(x.label for x in d.DETECTORS)
