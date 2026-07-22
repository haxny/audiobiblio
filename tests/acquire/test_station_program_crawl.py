"""Station-site program crawl — the user's reference hierarchy:

    stanice  https://olomouc.rozhlas.cz
    pořad    https://olomouc.rozhlas.cz/poctenicko-6370902
    kniha    https://olomouc.rozhlas.cz/anna-strnadova-...-9617888   (12 dílů)
    kniha    https://olomouc.rozhlas.cz/alena-mornstajnova-...-9624338 (1 díl)

Program pages are HTML listings yt-dlp cannot read; article links carry
7+ digit ids. Books must become their OWN works named by the book (the
old flow named works after the parent program — the very reason the
segmentation engine had to exist).
"""
from __future__ import annotations

import pytest

import audiobiblio.acquire.crawler as crawler_mod
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, CrawlTargetKind, Episode, Program, Station,
    Work,
)
from audiobiblio.sources.rozhlas_station import discover_articles, is_station_program_url
from audiobiblio.sources.mrz_inspector import EpisodeItem, ProbeResult

PROGRAM_URL = "https://olomouc.rozhlas.cz/poctenicko-6370902"
BOOK12_URL = "https://olomouc.rozhlas.cz/anna-strnadova-zivot-na-pavoucim-vlakne-dramaticke-osudy-nekolika-rodin-na-9617888"
BOOK1_URL = "https://olomouc.rozhlas.cz/alena-mornstajnova-konopnice-pribeh-o-ztracenem-detstvi-jedne-venkovske-holky-v-9624338"

PROGRAM_HTML = """
<html><head><title>Počteníčko | Český rozhlas Olomouc</title></head><body>
<h1>Počteníčko</h1>
<a href="/anna-strnadova-zivot-na-pavoucim-vlakne-dramaticke-osudy-nekolika-rodin-na-9617888">kniha 1</a>
<a href="/alena-mornstajnova-konopnice-pribeh-o-ztracenem-detstvi-jedne-venkovske-holky-v-9624338">kniha 2</a>
<a href="/kontakty-6472536">Kontakty</a>
<a href="/poctenicko-6370902">self</a>
<a href="https://olomouc.rozhlas.cz/anna-strnadova-zivot-na-pavoucim-vlakne-dramaticke-osudy-nekolika-rodin-na-9617888">dup abs</a>
</body></html>
"""


class TestDiscovery:
    def test_is_station_program_url(self):
        assert is_station_program_url(PROGRAM_URL)
        assert is_station_program_url("https://vltava.rozhlas.cz/cokoli-1234567")
        assert not is_station_program_url("https://www.mujrozhlas.cz/cetba-s-hvezdickou")
        assert not is_station_program_url("https://example.com/x-1234567")

    def test_discover_articles_absolute_deduped_without_self(self):
        urls = discover_articles(PROGRAM_HTML, PROGRAM_URL)
        assert BOOK12_URL in urls
        assert BOOK1_URL in urls
        assert PROGRAM_URL not in urls, "self link excluded"
        assert len([u for u in urls if u == BOOK12_URL]) == 1, "deduped"
        # nav page has a 7-digit id too — it stays in the list; the probe
        # step filters non-audio pages naturally
        assert all(u.startswith("https://olomouc.rozhlas.cz/") for u in urls)


def _book_pr(url: str, title: str, n: int, first_ext: int) -> ProbeResult:
    return ProbeResult(
        kind="series", url=url, title=title, series=None,
        uploader=None, extractor="RozhlasVltava",
        entries=[
            EpisodeItem(url=url, title=title, episode_number=None,
                        ext_id=str(first_ext + i))
            for i in range(n)
        ],
    )


@pytest.fixture()
def station_target(db_session):
    t = CrawlTarget(
        url=PROGRAM_URL,
        kind=CrawlTargetKind.PROGRAM,
        approval_mode=ApprovalMode.AUTO,
        interval_hours=24,
    )
    db_session.add(t)
    db_session.commit()
    return t


class TestStationCrawl:
    def _run(self, db_session, target, monkeypatch):
        pages = {
            BOOK12_URL: _book_pr(BOOK12_URL,
                                 "Anna Strnadová: Život na pavoučím vlákně",
                                 12, 12070034),
            BOOK1_URL: _book_pr(BOOK1_URL,
                                "Alena Mornštajnová: Konopnice",
                                1, 12084637),
        }

        def fake_probe(url):
            if url in pages:
                return {"stub": url}
            raise RuntimeError(f"yt-dlp cannot read {url}")

        monkeypatch.setattr(crawler_mod, "probe_url", fake_probe)
        monkeypatch.setattr(crawler_mod, "classify_probe",
                            lambda data, url: pages[url])
        monkeypatch.setattr(
            crawler_mod, "fetch_station_page",
            lambda url: ("Počteníčko", PROGRAM_HTML))
        from audiobiblio.sources.rozhlas_station import ArticleStub
        monkeypatch.setattr(
            crawler_mod, "fetch_archive_stubs",
            lambda url: [
                ArticleStub(url=BOOK12_URL, title="kniha 1",
                            published_at=None, perex=None),
                ArticleStub(url=BOOK1_URL, title="kniha 2",
                            published_at=None, perex=None),
            ])
        crawler_mod.crawl_target(target, session=db_session)

    def test_program_named_correctly_under_station(self, db_session, station_target, monkeypatch):
        self._run(db_session, station_target, monkeypatch)
        prog = db_session.query(Program).filter_by(name="Počteníčko").first()
        assert prog is not None, "program named from the page, not 'mujrozhlas'"
        assert prog.station.code == "CRoOl"

    def test_each_book_is_its_own_work(self, db_session, station_target, monkeypatch):
        self._run(db_session, station_target, monkeypatch)
        works = {w.title: w for w in db_session.query(Work).all()}
        assert "Anna Strnadová: Život na pavoučím vlákně" in works
        assert "Alena Mornštajnová: Konopnice" in works
        assert "Počteníčko" not in works, "no catch-all program work"

    def test_part_counts(self, db_session, station_target, monkeypatch):
        self._run(db_session, station_target, monkeypatch)
        w12 = db_session.query(Work).filter(
            Work.title.like("Anna Strnadová%")).one()
        w1 = db_session.query(Work).filter(
            Work.title.like("Alena Mornštajnová%")).one()
        assert db_session.query(Episode).filter_by(work_id=w12.id).count() == 12
        assert db_session.query(Episode).filter_by(work_id=w1.id).count() == 1

    def test_idempotent_recrawl(self, db_session, station_target, monkeypatch):
        self._run(db_session, station_target, monkeypatch)
        self._run(db_session, station_target, monkeypatch)
        assert db_session.query(Episode).count() == 13
