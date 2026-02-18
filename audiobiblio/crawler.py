"""
crawler — Discover episodes from CrawlTarget URLs and upsert to DB.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import structlog
from sqlalchemy import select

from .db.models import CrawlTarget, CrawlTargetKind, Episode, AvailabilityStatus
from .db.session import get_session
from .mrz_inspector import (
    probe_url, classify_probe, deep_probe_kind,
    mrz_discover_children, mrz_discover_children_depth,
    _mrz_depth,
)
from .pipelines.ingest import upsert_from_item, queue_assets_for_episode

log = structlog.get_logger()


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    from urllib.parse import urlparse, urlunparse
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path[:-1] if p.path.endswith("/") else p.path
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return u.strip().rstrip("/")


def crawl_target(target: CrawlTarget, session=None) -> int:
    """
    Crawl a single CrawlTarget.
    Discovers episodes, upserts them, queues downloads for auto-download episodes.
    Returns the number of new jobs queued.
    """
    s = session or get_session()
    url = target.url
    total_jobs = 0

    log.info("crawl_start", url=url, kind=target.kind.value)

    try:
        data = probe_url(url)
        pr = classify_probe(data, url)
    except Exception as e:
        log.error("crawl_probe_failed", url=url, error=str(e))
        target.last_crawled_at = datetime.utcnow()
        target.next_crawl_at = datetime.utcnow() + timedelta(hours=target.interval_hours)
        s.commit()
        return 0

    # Single episode
    if pr.kind == "episode" and pr.entries:
        item = pr.entries[0]
        total_jobs += _ingest_episode(s, item, pr)

    # Container (program/series/playlist)
    else:
        entries = _discover_entries(pr, url)
        seen = set()
        for idx, e in enumerate(entries, 1):
            eu = _norm_url(getattr(e, "url", None))
            if not eu or eu in seen or eu == _norm_url(url):
                continue
            seen.add(eu)

            try:
                kind = deep_probe_kind(e.url)
            except Exception:
                kind = "episode"

            if kind == "episode":
                ep_num = getattr(e, "episode_number", None) or idx
                total_jobs += _ingest_episode_from_entry(s, e, pr, ep_num)
            elif kind == "series":
                total_jobs += _expand_series(s, e, pr)

    # Update target timestamps
    target.last_crawled_at = datetime.utcnow()
    target.next_crawl_at = datetime.utcnow() + timedelta(hours=target.interval_hours)
    s.commit()

    log.info("crawl_done", url=url, jobs_queued=total_jobs)
    return total_jobs


def _discover_entries(pr, url: str) -> list:
    """Discover child entries from a container probe result."""
    depth = _mrz_depth(url)

    if pr.extractor == "MujRozhlas":
        if pr.kind == "program" and depth == 1:
            # Use multi-source discovery for program-level URLs
            try:
                from .discovery import discover_program
                discovered = discover_program(url)
                if discovered:
                    # Convert DiscoveredEpisode to EI-like objects for compatibility
                    entries = [
                        type("EI", (), {
                            "url": ep.url, "title": ep.title, "series": ep.series or pr.title,
                            "episode_number": None, "author": ep.author, "uploader": ep.uploader or pr.uploader,
                        })
                        for ep in discovered
                    ]
                    return entries
            except Exception as exc:
                log.warning("discover_program_fallback", url=url, error=str(exc))

            # Fallback to HTML discovery
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title,
                                "episode_number": None, "author": None, "uploader": pr.uploader})
                for (u, t) in mrz_discover_children_depth(url, want_depth=2)
            ]
            if not entries:
                entries = pr.entries or []
            return entries
        elif pr.kind == "series" and depth == 2:
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title,
                                "episode_number": None, "author": None, "uploader": pr.uploader})
                for (u, t) in mrz_discover_children_depth(url, want_depth=3)
            ]
            if not entries:
                entries = pr.entries or []
            return entries

    return pr.entries or []


def _ingest_episode(s, item, pr) -> int:
    """Ingest a single episode item."""
    ep, _work = upsert_from_item(
        s,
        url=item.url,
        item_title=item.title,
        series_name=item.series or pr.series or pr.title,
        author=item.author,
        uploader=item.uploader or pr.uploader,
        work_title=pr.title if pr.series else item.series or item.title,
        episode_number=item.episode_number or 1,
    )
    _update_availability(ep)
    jobs = queue_assets_for_episode(s, ep.id)
    return len(jobs)


def _ingest_episode_from_entry(s, e, pr, ep_num: int) -> int:
    """Ingest an episode from a discovered entry."""
    ep, _work = upsert_from_item(
        s,
        url=e.url,
        item_title=getattr(e, "title", ""),
        series_name=getattr(e, "series", None) or pr.series or pr.title,
        author=getattr(e, "author", None),
        uploader=getattr(e, "uploader", None) or pr.uploader,
        work_title=pr.title or getattr(e, "series", None) or getattr(e, "title", ""),
        episode_number=ep_num,
    )
    _update_availability(ep)
    jobs = queue_assets_for_episode(s, ep.id)
    return len(jobs)


def _expand_series(s, e, pr) -> int:
    """Expand a series entry into individual episodes."""
    total = 0
    try:
        child_entries = [
            type("EI", (), {"url": u, "title": t, "series": pr.title,
                            "episode_number": None, "author": None, "uploader": pr.uploader})
            for (u, t) in mrz_discover_children_depth(e.url, want_depth=_mrz_depth(e.url) + 1)
        ]
        if not child_entries:
            child = classify_probe(probe_url(e.url), e.url)
            child_entries = child.entries or []

        seen = set()
        for j, ce in enumerate(child_entries, 1):
            cu = _norm_url(getattr(ce, "url", None))
            if not cu or cu in seen:
                continue
            seen.add(cu)
            ep_num = getattr(ce, "episode_number", None) or j
            total += _ingest_episode_from_entry(s, ce, pr, ep_num)
    except Exception as exc:
        log.error("expand_series_failed", url=e.url, error=str(exc))

    return total


def _update_availability(ep: Episode):
    """Update availability tracking fields on an episode."""
    now = datetime.utcnow()
    if ep.first_seen_at is None:
        ep.first_seen_at = now
    ep.last_seen_at = now
    ep.last_checked_at = now
    ep.availability_status = AvailabilityStatus.AVAILABLE


def run_due_crawls() -> int:
    """Run all crawl targets that are due. Returns total jobs queued."""
    s = get_session()
    now = datetime.utcnow()
    targets = s.query(CrawlTarget).filter(
        CrawlTarget.active == True,
        (CrawlTarget.next_crawl_at <= now) | (CrawlTarget.next_crawl_at.is_(None))
    ).all()

    total = 0
    for t in targets:
        try:
            total += crawl_target(t, session=s)
        except Exception as e:
            log.error("crawl_target_error", url=t.url, error=str(e))
            t.last_crawled_at = now
            t.next_crawl_at = now + timedelta(hours=t.interval_hours)
            s.commit()

    return total
