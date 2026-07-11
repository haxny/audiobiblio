"""Multi-part books: mujrozhlas embeds ALL parts of a book on ONE page URL,
distinguished only by yt-dlp entry ext_id + episode_number.

Two crawler paths used to URL-dedupe the parts away (only part 1 survived):
  1. _expand_series children loop — `seen` keyed by URL alone.
  2. crawl_target main loop — same, plus `eu == target url` drop when a
     series target's pr.entries fall back to the same-URL parts.

Found live: "Příběh služebnice" (12 parts) stayed a single episode even
after the 0.7.2 ext_id identity fix, because parts 2–12 never reached
dedupe/ingest at all.
"""
from __future__ import annotations

import pytest

import audiobiblio.acquire.crawler as crawler_mod
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, CrawlTargetKind, Episode,
)
from audiobiblio.sources.mrz_inspector import EpisodeItem, ProbeResult

BOOK_URL = (
    "https://www.mujrozhlas.cz/cetba-s-hvezdickou/"
    "margaret-attwoodova-pribeh-sluzebnice"
)
PROGRAM_URL = "https://www.mujrozhlas.cz/cetba-s-hvezdickou"


def _parts(n: int) -> list[EpisodeItem]:
    """N parts of one book: identical page URL + title, distinct ext_ids."""
    return [
        EpisodeItem(
            url=BOOK_URL,
            title="Margaret Atwoodová: Příběh služebnice",
            episode_number=i,
            series="Četba s hvězdičkou",
            uploader="mujrozhlas",
            ext_id=str(12087680 + i),
            duration_s=1500.0,
        )
        for i in range(1, n + 1)
    ]


def test_expand_series_ingests_all_same_url_parts(db_session, monkeypatch):
    parent_pr = ProbeResult(
        kind="program", url=PROGRAM_URL, title="Četba s hvězdičkou",
        series=None, uploader="mujrozhlas", extractor="mujrozhlas",
    )
    entry = EpisodeItem(url=BOOK_URL, title="Margaret Atwoodová: Příběh služebnice")
    child_pr = ProbeResult(
        kind="series", url=BOOK_URL,
        title="Margaret Atwoodová: Příběh služebnice",
        series="Četba s hvězdičkou", uploader="mujrozhlas",
        extractor="mujrozhlas", entries=_parts(12),
    )

    monkeypatch.setattr(
        crawler_mod, "mrz_discover_children_depth", lambda url, want_depth: [])
    monkeypatch.setattr(crawler_mod, "probe_url", lambda url: {"stub": True})
    monkeypatch.setattr(crawler_mod, "classify_probe", lambda data, url: child_pr)

    crawler_mod._expand_series(db_session, entry, parent_pr, approval_mode=None)

    eps = db_session.query(Episode).all()
    assert len(eps) == 12, "every part must become its own episode"
    assert sorted(e.ext_id for e in eps) == sorted(p.ext_id for p in _parts(12))
    numbers = sorted(e.episode_number for e in eps)
    assert numbers == list(range(1, 13))


def test_crawl_series_target_ingests_all_parts(db_session, monkeypatch):
    """A CrawlTarget pointing straight at the book page must ingest all
    parts — previously every part was dropped because its URL equals the
    target URL."""
    target = CrawlTarget(
        url=BOOK_URL,
        kind=CrawlTargetKind.PROGRAM,
        approval_mode=ApprovalMode.REVIEW,
        interval_hours=24,
    )
    db_session.add(target)
    db_session.commit()

    book_pr = ProbeResult(
        kind="series", url=BOOK_URL,
        title="Margaret Atwoodová: Příběh služebnice",
        series="Četba s hvězdičkou", uploader="mujrozhlas",
        extractor="mujrozhlas", entries=_parts(12),
    )
    monkeypatch.setattr(crawler_mod, "probe_url", lambda url: {"stub": True})
    monkeypatch.setattr(crawler_mod, "classify_probe", lambda data, url: book_pr)
    monkeypatch.setattr(
        crawler_mod, "mrz_discover_children_depth", lambda url, want_depth: [])
    # deep_probe_kind must NOT be needed for entries that carry ext_id —
    # a concrete media id IS an episode; probing it again is a network trip.
    monkeypatch.setattr(
        crawler_mod, "deep_probe_kind",
        lambda url: pytest.fail("deep_probe_kind called for an ext_id entry"))

    crawler_mod.crawl_target(target, session=db_session)

    eps = db_session.query(Episode).all()
    assert len(eps) == 12
    assert sorted(e.ext_id for e in eps) == sorted(p.ext_id for p in _parts(12))
