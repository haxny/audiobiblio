"""Archive indexing: EVERY aired episode gets a record — air date + perex
from the station archive cards — even when its audio is no longer online
(the SFT reconstruction case: hundreds of aired episodes, completable
whenever a re-air appears via the GONE-revive mechanism)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import audiobiblio.acquire.crawler as crawler_mod
from audiobiblio.core.db.models import (
    ApprovalMode, Asset, AssetStatus, AssetType, AvailabilityStatus,
    CrawlTarget, CrawlTargetKind, DownloadJob, Episode,
)
from audiobiblio.sources.rozhlas_station import (
    ArticleStub, discover_article_stubs, fetch_archive_stubs, parse_czech_date,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sft_archive_page.html"
BASE = "https://dvojka.rozhlas.cz/stopy-fakta-tajemstvi-6483239"


class TestParsing:
    def test_czech_dates(self):
        assert parse_czech_date("20. červenec 2026") == datetime(2026, 7, 20)
        assert parse_czech_date("1. ledna 2019") == datetime(2019, 1, 1)
        assert parse_czech_date("nesmysl") is None

    def test_cards_from_real_fixture(self):
        stubs = discover_article_stubs(FIXTURE.read_text(), BASE)
        assert len(stubs) >= 2
        first = stubs[0]
        assert first.title.startswith("Krásná Elsa a rada Vacátko")
        assert first.published_at == datetime(2026, 7, 20)
        assert "Vacátka" in first.perex
        assert first.url.startswith("https://dvojka.rozhlas.cz/krasna-elsa")

    def test_pagination_stops_when_no_new(self):
        html = FIXTURE.read_text()
        pages = {BASE: html, f"{BASE}?page=1": html}  # page 1 repeats → stop

        def fake_fetch(u):
            return pages.get(u, "")

        stubs = fetch_archive_stubs(BASE, fetch=fake_fetch)
        urls = [s.url for s in stubs]
        assert len(urls) == len(set(urls)), "no duplicates across pages"


class TestStubIngest:
    def test_gone_stub_indexed_without_jobs(self, db_session, monkeypatch):
        # hermetic: no network — pair derivation is exercised in
        # tests/sources/test_pairing.py
        monkeypatch.setattr(
            "audiobiblio.sources.pairing.derive_mujrozhlas_counterpart",
            lambda url: None)
        target = CrawlTarget(
            url=BASE, kind=CrawlTargetKind.PROGRAM,
            approval_mode=ApprovalMode.AUTO, interval_hours=24)
        db_session.add(target)
        db_session.commit()

        stub = ArticleStub(
            url="https://dvojka.rozhlas.cz/ufo-nad-prahou-7457879",
            title="UFO nad Prahou",
            published_at=datetime(2020, 5, 3),
            perex="Patrani Stanislava Motla po zahade.")

        monkeypatch.setattr(
            crawler_mod, "fetch_station_page",
            lambda url: ("Stopy, fakta, tajemství", ""))
        monkeypatch.setattr(
            crawler_mod, "fetch_archive_stubs", lambda url: [stub])

        def failing_probe(url):
            raise RuntimeError("audio gone")
        monkeypatch.setattr(crawler_mod, "probe_url", failing_probe)

        crawler_mod.crawl_target(target, session=db_session)

        ep = db_session.query(Episode).filter_by(url=stub.url).one()
        assert ep.published_at == datetime(2020, 5, 3)
        assert ep.summary == "Patrani Stanislava Motla po zahade."
        assert ep.availability_status == AvailabilityStatus.GONE
        audio = db_session.query(Asset).filter_by(
            episode_id=ep.id, type=AssetType.AUDIO).one()
        assert audio.status == AssetStatus.MISSING
        assert db_session.query(DownloadJob).count() == 0, \
            "stub must NOT queue downloads (they would only error)"

    def test_second_crawl_backfills_without_reprobe(self, db_session, monkeypatch):
        self.test_gone_stub_indexed_without_jobs(db_session, monkeypatch)
        calls = []

        def counting_probe(url):
            calls.append(url)
            raise RuntimeError("x")
        monkeypatch.setattr(crawler_mod, "probe_url", counting_probe)

        target = db_session.query(CrawlTarget).first()
        crawler_mod.crawl_target(target, session=db_session)
        # the program URL itself is always probed first (yt-dlp fails →
        # station branch); ARTICLE urls must not be re-probed
        assert calls == [BASE], "known articles are never re-probed by the crawl"
        assert db_session.query(Episode).count() == 1


class TestRelatedPlayerFilter:
    def test_foreign_promos_dropped(self):
        from audiobiblio.sources.rozhlas_station import filter_serial_entries
        from audiobiblio.sources.mrz_inspector import EpisodeItem
        parts = [EpisodeItem(url="u", title="Lenka Elbe: URaNovA", ext_id=str(i))
                 for i in range(10)]
        promo = [
            EpisodeItem(url="u", title="Tanec se slovy v rytmu jazzu. Oslavte s Vltavou…", ext_id="x1"),
            EpisodeItem(url="u", title="Jaroslav Hašek: Z dějin Strany mírného pokroku", ext_id="x2"),
        ]
        kept, dropped = filter_serial_entries(parts + promo, "Lenka Elbe: URaNovA. Jáchymov…")
        assert len(kept) == 10 and len(dropped) == 2

    def test_no_confident_majority_keeps_all(self):
        from audiobiblio.sources.rozhlas_station import filter_serial_entries
        from audiobiblio.sources.mrz_inspector import EpisodeItem
        mixed = [EpisodeItem(url="u", title=f"Kapitola {i}", ext_id=str(i)) for i in range(5)]
        kept, dropped = filter_serial_entries(mixed, "Kniha X")
        assert len(kept) == 5 and not dropped
