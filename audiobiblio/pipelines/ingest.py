from __future__ import annotations
import re
from datetime import datetime
from sqlalchemy import select
from typing import Optional
from urllib.parse import urlparse, urlunparse

import structlog

from ..db.session import get_session
from ..db.models import (
    Station, Program, Series, Work, Episode, EpisodeAlias,
    AvailabilityStatus, DownloadJob, JobStatus,
)
from ..pipelines.checks import plan_downloads

log = structlog.get_logger()

# Trailing numeric re-air suffix pattern
_REAIR_SUFFIX_RE = re.compile(r"-\d{7,}$")


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = p.path.rstrip("/")
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return u.strip().rstrip("/")


def _norm_url_strip_reair(u: str | None) -> str:
    norm = _norm_url(u)
    if not norm:
        return ""
    try:
        p = urlparse(norm)
        path = _REAIR_SUFFIX_RE.sub("", p.path)
        return urlunparse((p.scheme, p.netloc, path, "", "", ""))
    except Exception:
        return norm


def _get_or_create_station(session, code: str, name: Optional[str], website: Optional[str]):
    st = session.query(Station).filter_by(code=code).first()
    if st: return st
    st = Station(code=code, name=name or code, website=website)
    session.add(st); session.flush()
    return st

def _guess_station_from_uploader(uploader: Optional[str]) -> tuple[str, str|None, str|None]:
    if not uploader:
        return ("mujrozhlas", "mujrozhlas.cz", "https://www.mujrozhlas.cz")
    u = uploader.lower()
    if "vltava" in u: return ("CRo3", "Vltava", "https://vltava.rozhlas.cz")
    if "dvojka" in u: return ("CRo2", "Dvojka", "https://dvojka.rozhlas.cz")
    if "radiozurnal" in u or "radiožurnál" in u: return ("CRo1", "Radiožurnál", "https://radiozurnal.rozhlas.cz")
    if "junior" in u: return ("CRoJun", "Rádio Junior", "https://junior.rozhlas.cz")
    if "plus" in u: return ("CRoPlus", "Plus", "https://plus.rozhlas.cz")
    if "wave" in u: return ("CRoW", "Wave", "https://wave.rozhlas.cz")
    return ("mujrozhlas", "mujrozhlas.cz", "https://www.mujrozhlas.cz")


def _add_alias(session, episode: Episode, url: str, ext_id: str | None = None,
               discovery_source: str | None = None):
    """Add an EpisodeAlias if it doesn't already exist."""
    norm = _norm_url(url)
    existing = session.query(EpisodeAlias).filter_by(
        episode_id=episode.id, url=norm
    ).first()
    if not existing:
        alias = EpisodeAlias(
            episode_id=episode.id,
            url=norm,
            ext_id=ext_id,
            discovery_source=discovery_source,
        )
        session.add(alias)
        log.debug("alias_added", episode_id=episode.id, url=norm)


def _find_existing_episode(session, url: str, ext_id: str | None, work: Work | None):
    """
    Check for an existing episode that matches this URL or ext_id.
    Returns (episode, match_reason) or (None, None).
    """
    # 1. ext_id match on Episode
    if ext_id:
        ep = session.query(Episode).filter_by(ext_id=ext_id).first()
        if ep:
            return ep, "ext_id"

    # 2. URL match on EpisodeAlias
    norm = _norm_url(url)
    if norm:
        alias = session.query(EpisodeAlias).filter_by(url=norm).first()
        if alias:
            ep = session.get(Episode, alias.episode_id)
            if ep:
                return ep, "alias_url"

    # 3. Stripped URL match (re-air detection) on Episode.url
    stripped = _norm_url_strip_reair(url)
    if stripped and stripped != norm:
        # Check all episodes in the same work
        if work:
            for ep in work.episodes:
                ep_stripped = _norm_url_strip_reair(ep.url)
                if ep_stripped == stripped:
                    return ep, "url_reair"

    return None, None


def _maybe_revive_gone_episode(session, ep: Episode, new_url: str):
    """
    If an episode is GONE but we have a working re-air URL, update it
    and re-queue downloads.
    """
    if ep.availability_status != AvailabilityStatus.GONE:
        return

    old_url = ep.url
    ep.url = new_url
    ep.availability_status = AvailabilityStatus.AVAILABLE
    ep.last_seen_at = datetime.utcnow()
    log.info("revived_gone_episode", episode_id=ep.id, old_url=old_url, new_url=new_url)

    # Re-queue failed/pending jobs
    failed_jobs = session.query(DownloadJob).filter(
        DownloadJob.episode_id == ep.id,
        DownloadJob.status.in_([JobStatus.ERROR, JobStatus.WATCH]),
    ).all()
    for job in failed_jobs:
        job.status = JobStatus.PENDING
        job.error = None
        log.info("requeued_job", job_id=job.id, episode_id=ep.id)


def upsert_from_item(session, *,
                     url: str,
                     item_title: str,
                     series_name: Optional[str],
                     author: Optional[str],
                     uploader: Optional[str],
                     program_name: Optional[str] = None,
                     work_title: Optional[str] = None,
                     episode_number: Optional[int] = None,
                     ext_id: Optional[str] = None,
                     discovery_source: Optional[str] = None,
                     priority: int = 0,
                     summary: Optional[str] = None,
                     published_at: Optional[datetime] = None,
                     duration_ms: Optional[int] = None):
    # Station
    code, st_name, st_url = _guess_station_from_uploader(uploader)
    st = _get_or_create_station(session, code=code, name=st_name, website=st_url)

    # Program (unknown unless we can infer; use uploader as placeholder)
    prog_name = program_name or uploader or "mujrozhlas"
    prog = session.query(Program).filter_by(station_id=st.id, name=prog_name).first()
    if not prog:
        prog = Program(station_id=st.id, name=prog_name, url=st_url)
        session.add(prog); session.flush()

    # Series
    series_name = series_name or prog_name
    series = session.query(Series).filter_by(program_id=prog.id, name=series_name).first()
    if not series:
        series = Series(program_id=prog.id, name=series_name, url=url)
        session.add(series); session.flush()

    # Work
    work_title = work_title or series_name
    work = session.query(Work).filter_by(series_id=series.id, title=work_title).first()
    if not work:
        work = Work(series_id=series.id, title=work_title, author=author)
        session.add(work); session.flush()

    # Re-air / alias detection before creating a new Episode
    existing_ep, match_reason = _find_existing_episode(session, url, ext_id, work)
    if existing_ep:
        # This is a known episode — add alias and possibly revive
        _add_alias(session, existing_ep, url, ext_id=ext_id, discovery_source=discovery_source)
        _maybe_revive_gone_episode(session, existing_ep, url)
        # Update metadata if richer
        if item_title and (not existing_ep.title or len(item_title) > len(existing_ep.title)):
            existing_ep.title = item_title
        if ext_id and not existing_ep.ext_id:
            existing_ep.ext_id = ext_id
        if discovery_source:
            existing_ep.discovery_source = discovery_source
        if priority and priority > existing_ep.priority:
            existing_ep.priority = priority
        # Enrich with richer metadata (non-empty replaces empty)
        if summary and not existing_ep.summary:
            existing_ep.summary = summary
        if published_at and not existing_ep.published_at:
            existing_ep.published_at = published_at
        if duration_ms and not existing_ep.duration_ms:
            existing_ep.duration_ms = duration_ms
        session.commit()
        log.debug("upsert_existing", episode_id=existing_ep.id, reason=match_reason)
        return existing_ep, work

    # Episode — check by work_id + episode_number (original logic)
    ep = session.query(Episode).filter_by(
        work_id=work.id, episode_number=episode_number
    ).first() if episode_number is not None else None

    if not ep:
        ep = Episode(
            work_id=work.id,
            episode_number=episode_number,
            title=item_title or f"Episode {episode_number or 1}",
            url=url,
            ext_id=ext_id,
            discovery_source=discovery_source,
            priority=priority,
            summary=summary,
            published_at=published_at,
            duration_ms=duration_ms,
        )
        session.add(ep)
    else:
        ep.title = item_title or ep.title
        ep.url = url or ep.url
        if ext_id and not ep.ext_id:
            ep.ext_id = ext_id
        if discovery_source:
            ep.discovery_source = discovery_source
        if priority and priority > ep.priority:
            ep.priority = priority
        if summary and not ep.summary:
            ep.summary = summary
        if published_at and not ep.published_at:
            ep.published_at = published_at
        if duration_ms and not ep.duration_ms:
            ep.duration_ms = duration_ms
    session.commit()

    # Also add the URL as an alias for future dedup
    _add_alias(session, ep, url, ext_id=ext_id, discovery_source=discovery_source)
    session.commit()

    return ep, work

def queue_assets_for_episode(session, episode_id: int):
    return plan_downloads(session, episode_id)
