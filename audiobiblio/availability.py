"""
availability — Probe episode URLs to track availability without downloading.

Czech Radio content can appear and disappear within days/weeks.
This module checks whether episodes are still reachable and updates their status.
"""
from __future__ import annotations
from datetime import datetime
import structlog
import requests
from sqlalchemy import select, or_

from .db.models import (
    Episode, AvailabilityLog, AvailabilityStatus,
    DownloadJob, JobStatus,
)
from .db.session import get_session
from .pipelines.checks import plan_downloads

log = structlog.get_logger()

# Full browser User-Agent required by mujrozhlas.cz
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def check_episode_availability(session, episode: Episode) -> AvailabilityStatus:
    """
    Probe an episode's URL with a HEAD request (fallback to GET).
    Updates episode fields and creates an AvailabilityLog entry.
    Returns the new status.
    """
    url = episode.url
    if not url:
        return AvailabilityStatus.UNKNOWN

    now = datetime.utcnow()
    http_status = None
    available = False

    try:
        r = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        http_status = r.status_code
        if http_status == 405:
            # HEAD not allowed, try GET
            r = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True, stream=True)
            http_status = r.status_code
            r.close()
        available = 200 <= http_status < 400
    except requests.RequestException as e:
        log.warning("availability_check_error", url=url, error=str(e))

    # Determine status
    if available:
        new_status = AvailabilityStatus.AVAILABLE
    elif http_status and http_status in (404, 410):
        new_status = AvailabilityStatus.GONE
    else:
        new_status = AvailabilityStatus.UNAVAILABLE

    # Update episode
    episode.availability_status = new_status
    episode.last_checked_at = now
    if available:
        episode.last_seen_at = now

    # Log entry
    entry = AvailabilityLog(
        episode_id=episode.id,
        checked_at=now,
        was_available=available,
        http_status=http_status,
    )
    session.add(entry)
    session.commit()

    log.info("availability_checked",
             episode_id=episode.id, url=url,
             status=new_status.value, http=http_status)
    return new_status


def check_unknown_episodes(limit: int = 50) -> int:
    """
    Check availability for episodes with UNKNOWN or UNAVAILABLE status.
    Designed to run as a scheduled job 2-4x daily.
    Returns number checked.
    """
    s = get_session()
    episodes = s.query(Episode).filter(
        Episode.url.isnot(None),
        or_(
            Episode.availability_status == AvailabilityStatus.UNKNOWN,
            Episode.availability_status == AvailabilityStatus.UNAVAILABLE,
            Episode.availability_status.is_(None),
        )
    ).limit(limit).all()

    checked = 0
    for ep in episodes:
        check_episode_availability(s, ep)
        checked += 1
    return checked


def process_watch_list() -> int:
    """
    Check WATCH jobs: probe URLs of episodes with WATCH status.
    If content reappears, re-queue as PENDING.
    Returns number of re-queued jobs.
    """
    s = get_session()
    watch_jobs = s.query(DownloadJob).filter(
        DownloadJob.status == JobStatus.WATCH
    ).all()

    requeued = 0
    for job in watch_jobs:
        ep = s.get(Episode, job.episode_id)
        if not ep or not ep.url:
            continue

        status = check_episode_availability(s, ep)
        if status == AvailabilityStatus.AVAILABLE:
            job.status = JobStatus.PENDING
            job.error = (job.error or "") + f"\nRe-queued: content reappeared at {datetime.utcnow()}"
            s.commit()
            requeued += 1
            log.info("watch_requeued", job_id=job.id, episode_id=ep.id)

    return requeued
