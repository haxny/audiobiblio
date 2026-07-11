"""
crawler — Discover episodes from CrawlTarget URLs and upsert to DB.
"""
from __future__ import annotations
from datetime import timedelta
import structlog

from audiobiblio.core.time import utcnow
from sqlalchemy import select

from audiobiblio.core.urls import norm_url as _norm_url
from audiobiblio.core.db.models import CrawlTarget, CrawlTargetKind, Episode, AvailabilityStatus
from audiobiblio.core.db.session import get_session
from audiobiblio.sources.mrz_inspector import (
    probe_url, classify_probe, deep_probe_kind,
    mrz_discover_children, mrz_discover_children_depth,
    _mrz_depth,
)
from audiobiblio.library.pipelines.ingest import upsert_from_item, queue_assets_for_episode

log = structlog.get_logger()


def target_state(target: CrawlTarget, now: datetime) -> str:
    """Classify a CrawlTarget's freshness relative to *now*.

    Returns one of:
      "inactive" — target.active is False
      "overdue"  — next_crawl_at is more than 0.5 × interval_hours in the past
      "due"      — next_crawl_at <= now (or is None)
      "ok"       — next_crawl_at is in the future
    """
    if not target.active:
        return "inactive"

    nca = target.next_crawl_at
    if nca is None:
        return "due"

    if nca > now:
        return "ok"

    grace = timedelta(hours=target.interval_hours * 0.5)
    if nca < now - grace:
        return "overdue"

    return "due"


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
        db_target = s.get(CrawlTarget, target.id)
        if db_target is not None:
            db_target.last_crawled_at = utcnow()
            db_target.next_crawl_at = utcnow() + timedelta(hours=db_target.interval_hours)
            s.commit()
        return 0

    approval_mode = target.approval_mode

    # Single episode
    if pr.kind == "episode" and pr.entries:
        item = pr.entries[0]
        total_jobs += _ingest_episode(s, item, pr, approval_mode)

    # Container (program/series/playlist)
    else:
        entries = _discover_entries(pr, url)
        seen = set()
        for idx, e in enumerate(entries, 1):
            eu = _norm_url(getattr(e, "url", None))
            ext = getattr(e, "ext_id", None)
            # Multi-part books share ONE page URL across all parts — ext_id
            # is the only per-part identity, so it must drive dedup and the
            # self-URL guard (a series target's parts ARE the target URL).
            key = ("ext", ext) if ext else ("url", eu)
            if not eu or key in seen or (not ext and eu == _norm_url(url)):
                continue
            seen.add(key)

            if ext:
                # A concrete media id IS an episode — no probe round-trip.
                kind = "episode"
            else:
                try:
                    kind = deep_probe_kind(e.url)
                except Exception:
                    kind = "episode"

            if kind == "episode":
                ep_num = getattr(e, "episode_number", None) or idx
                total_jobs += _ingest_episode_from_entry(s, e, pr, ep_num, approval_mode)
            elif kind == "series":
                total_jobs += _expand_series(s, e, pr, approval_mode)

    # Update target timestamps — re-fetch by ID so this works whether `target`
    # is attached to `s` (scheduled path) or detached (manual crawl-now path).
    db_target = s.get(CrawlTarget, target.id)
    if db_target is not None:
        db_target.last_crawled_at = utcnow()
        db_target.next_crawl_at = utcnow() + timedelta(hours=db_target.interval_hours)
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
                from audiobiblio.sources.discovery import discover_program
                discovered = discover_program(url)
                if discovered:
                    # Convert DiscoveredEpisode to EI-like objects for compatibility
                    entries = [
                        type("EI", (), {
                            "url": ep.url, "title": ep.title, "series": ep.series or pr.title,
                            "episode_number": None, "author": ep.author, "uploader": ep.uploader or pr.uploader,
                            "ext_id": None,
                        })
                        for ep in discovered
                    ]
                    return entries
            except Exception as exc:
                log.warning("discover_program_fallback", url=url, error=str(exc))

            # Fallback to HTML discovery
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title,
                                "episode_number": None, "author": None, "uploader": pr.uploader,
                                "ext_id": None})
                for (u, t) in mrz_discover_children_depth(url, want_depth=2)
            ]
            if not entries:
                entries = pr.entries or []
            return entries
        elif pr.kind == "series" and depth == 2:
            entries = [
                type("EI", (), {"url": u, "title": t, "series": pr.title,
                                "episode_number": None, "author": None, "uploader": pr.uploader,
                                "ext_id": None})
                for (u, t) in mrz_discover_children_depth(url, want_depth=3)
            ]
            if not entries:
                entries = pr.entries or []
            return entries

    return pr.entries or []


def _ingest_episode(s, item, pr, approval_mode=None) -> int:
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
        ext_id=item.ext_id,
    )
    _update_availability(ep)
    jobs = queue_assets_for_episode(s, ep.id, approval_mode=approval_mode)
    return len(jobs)


def _ingest_episode_from_entry(s, e, pr, ep_num: int, approval_mode=None) -> int:
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
        ext_id=getattr(e, "ext_id", None),
    )
    _update_availability(ep)
    jobs = queue_assets_for_episode(s, ep.id, approval_mode=approval_mode)
    return len(jobs)


def _expand_series(s, e, pr, approval_mode=None) -> int:
    """Expand a series entry into individual episodes."""
    total = 0
    try:
        child_entries = [
            type("EI", (), {"url": u, "title": t, "series": pr.title,
                            "episode_number": None, "author": None, "uploader": pr.uploader,
                            "ext_id": None})
            for (u, t) in mrz_discover_children_depth(e.url, want_depth=_mrz_depth(e.url) + 1)
        ]
        if not child_entries:
            child = classify_probe(probe_url(e.url), e.url)
            child_entries = child.entries or []

        seen = set()
        for j, ce in enumerate(child_entries, 1):
            cu = _norm_url(getattr(ce, "url", None))
            ext = getattr(ce, "ext_id", None)
            # Same-URL parts are distinct episodes when they carry ext_ids —
            # URL-only dedup here silently dropped parts 2..N of every book.
            key = ("ext", ext) if ext else ("url", cu)
            if not cu or key in seen:
                continue
            seen.add(key)
            ep_num = getattr(ce, "episode_number", None) or j
            total += _ingest_episode_from_entry(s, ce, pr, ep_num, approval_mode)
    except Exception as exc:
        log.error("expand_series_failed", url=e.url, error=str(exc))

    return total


def _update_availability(ep: Episode):
    """Update availability tracking fields on an episode."""
    now = utcnow()
    if ep.first_seen_at is None:
        ep.first_seen_at = now
    ep.last_seen_at = now
    ep.last_checked_at = now
    ep.availability_status = AvailabilityStatus.AVAILABLE


def run_due_crawls() -> int:
    """Run all crawl targets that are due. Returns total jobs queued."""
    s = get_session()
    now = utcnow()
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
