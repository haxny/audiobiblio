from __future__ import annotations
import re
from datetime import datetime
from sqlalchemy import select, func
from typing import Optional
from urllib.parse import urlparse, urlunparse

import structlog

from audiobiblio.core.db.session import get_session
from audiobiblio.core.db.models import (
    Station, Program, Series, Work, Episode, EpisodeAlias,
    AvailabilityStatus, DownloadJob, JobStatus, FieldOrigin,
    Asset, AssetType, AssetStatus,
)
from audiobiblio.core.provenance import has_manual, record_value
from audiobiblio.dedupe.matching import is_generic_title
from audiobiblio.dedupe.upgrades import evaluate_reair
from audiobiblio.library.pipelines.checks import plan_downloads
from audiobiblio.library.pipelines.completeness import complete_audio_count

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
    if "brno" in u: return ("CRoBrno", "Brno", "https://brno.rozhlas.cz")
    return ("mujrozhlas", "mujrozhlas.cz", "https://www.mujrozhlas.cz")


def guess_station_from_url(url: Optional[str]) -> tuple[str, str|None, str|None] | None:
    """Guess station from a rozhlas.cz URL domain (more reliable than uploader)."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return None
    if "vltava" in netloc: return ("CRo3", "Vltava", "https://vltava.rozhlas.cz")
    if "dvojka" in netloc: return ("CRo2", "Dvojka", "https://dvojka.rozhlas.cz")
    if "radiozurnal" in netloc: return ("CRo1", "Radiožurnál", "https://radiozurnal.rozhlas.cz")
    if "junior" in netloc: return ("CRoJun", "Rádio Junior", "https://junior.rozhlas.cz")
    if "plus" in netloc: return ("CRoPlus", "Plus", "https://plus.rozhlas.cz")
    if "wave" in netloc: return ("CRoW", "Wave", "https://wave.rozhlas.cz")
    if "brno" in netloc: return ("CRoBrno", "Brno", "https://brno.rozhlas.cz")
    if "pardubice" in netloc: return ("CRoPA", "CRo Pardubice", "https://pardubice.rozhlas.cz")
    return None


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


def _apply_gap_fill_priority(session, ep: "Episode", work: "Work") -> None:
    """Boost ep.priority to 10 when the work has expected_total set and have < expected_total.

    Called only for newly created episodes (not for existing-episode updates).
    'have' is computed via a lightweight SQL count of COMPLETE AUDIO assets.
    Cross-source hunting engine is deferred (phase 5+).
    """
    if work.expected_total is None:
        return

    have = complete_audio_count(session, work.id)

    if have < work.expected_total:
        if ep.priority < 10:
            ep.priority = 10
        log.debug("gap_fill_priority", episode_id=ep.id, work_id=work.id,
                  have=have, expected=work.expected_total)


def upsert_from_item(session, *,
                     url: str,
                     item_title: str,
                     series_name: Optional[str],
                     author: Optional[str],
                     uploader: Optional[str],
                     program_name: Optional[str] = None,
                     program_url: Optional[str] = None,
                     source_url: Optional[str] = None,
                     genre: Optional[str] = None,
                     channel_label: Optional[str] = None,
                     work_title: Optional[str] = None,
                     episode_number: Optional[int] = None,
                     ext_id: Optional[str] = None,
                     discovery_source: Optional[str] = None,
                     priority: int = 0,
                     summary: Optional[str] = None,
                     published_at: Optional[datetime] = None,
                     duration_ms: Optional[int] = None):
    # Station — prefer URL-based detection (vltava.rozhlas.cz → CRo3), fall back to uploader
    station_info = guess_station_from_url(source_url) or _guess_station_from_uploader(uploader)
    code, st_name, st_url = station_info
    st = _get_or_create_station(session, code=code, name=st_name, website=st_url)

    # Program
    prog_name = program_name or uploader or "mujrozhlas"
    prog = session.query(Program).filter_by(station_id=st.id, name=prog_name).first()
    if not prog:
        prog = Program(station_id=st.id, name=prog_name, url=program_url or st_url)
        if genre:
            prog.genre = genre
        if channel_label:
            prog.channel_label = channel_label
        session.add(prog); session.flush()
    else:
        # Update genre/channel_label if provided and not already set
        if genre and not prog.genre:
            prog.genre = genre
        if channel_label and not prog.channel_label:
            prog.channel_label = channel_label

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
        # Re-air match: evaluate for upgrade candidate (ad-suspect detection, spec §4.2)
        # duration_ms is the candidate's duration from the caller (upsert_from_item param).
        # owned duration is taken from existing_ep.duration_ms inside evaluate_reair.
        if match_reason == "url_reair":
            try:
                evaluate_reair(session, existing_ep, url, candidate_duration_ms=duration_ms)
            except Exception:
                log.warning(
                    "evaluate_reair_failed",
                    episode_id=existing_ep.id,
                    candidate_url=url,
                    exc_info=True,
                )
        # Update metadata if richer — never overwrite a good title with a generic placeholder,
        # and never overwrite a MANUAL title with a scraped one.
        if item_title and not is_generic_title(item_title) and (
            not existing_ep.title or len(item_title) > len(existing_ep.title)
        ) and not has_manual(session, "episode", existing_ep.id, "title"):
            existing_ep.title = item_title
        # Guard: never overwrite a MANUAL author with a scraped one.
        # Only set author if empty AND no MANUAL override exists.
        if author and not existing_ep.work.author and not has_manual(session, "work", existing_ep.work_id, "author"):
            existing_ep.work.author = author
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
        # Record SCRAPED provenance observations (failure must never break ingest).
        # Author is recorded on existing_ep.work_id (the episode's actual Work), so that
        # the MANUAL guard and provenance queries always target the right entity.
        _prov_src = discovery_source or "scrape"
        _ep_title_prov = item_title if (item_title and not is_generic_title(item_title)) else None
        try:
            if _ep_title_prov is not None:
                record_value(session, "episode", existing_ep.id, "title", _ep_title_prov, FieldOrigin.SCRAPED, _prov_src)
            if summary:
                record_value(session, "episode", existing_ep.id, "description", summary, FieldOrigin.SCRAPED, _prov_src)
            if author:
                record_value(session, "work", existing_ep.work_id, "author", author, FieldOrigin.SCRAPED, _prov_src)
            record_value(session, "work", existing_ep.work_id, "title", work_title, FieldOrigin.SCRAPED, _prov_src)
        except Exception:
            log.warning("record_provenance_failed", episode_id=existing_ep.id, exc_info=True)
        session.commit()
        log.debug("upsert_existing", episode_id=existing_ep.id, reason=match_reason)
        return existing_ep, work

    # Neutralise generic/placeholder titles before any title assignment.
    # Falls through to the "Episode N" fallback below.
    if item_title and is_generic_title(item_title):
        item_title = None

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
        session.flush()
        # Gap-fill priority: if this work has a gap, boost priority to 10.
        # Deferred: cross-source hunting engine (phase 5+).
        _apply_gap_fill_priority(session, ep, work)
    else:
        if item_title and not has_manual(session, "episode", ep.id, "title"):
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
    # Flush so new ep gets a DB id before recording provenance
    session.flush()
    # Record SCRAPED provenance observations (failure must never break ingest).
    # item_title is None here if it was generic (guard applied at line above).
    # Decision: only record episode title when item_title survived the generic guard
    # (i.e. is not None at this point), so the "Episode N" fallback is never stored
    # as a provenance observation.
    _prov_src = discovery_source or "scrape"
    try:
        if item_title is not None:
            record_value(session, "episode", ep.id, "title", item_title, FieldOrigin.SCRAPED, _prov_src)
        if summary:
            record_value(session, "episode", ep.id, "description", summary, FieldOrigin.SCRAPED, _prov_src)
        if author:
            record_value(session, "work", work.id, "author", author, FieldOrigin.SCRAPED, _prov_src)
        record_value(session, "work", work.id, "title", work_title, FieldOrigin.SCRAPED, _prov_src)
    except Exception:
        log.warning("record_provenance_failed", episode_id=ep.id, exc_info=True)
    session.commit()

    # Also add the URL as an alias for future dedup
    _add_alias(session, ep, url, ext_id=ext_id, discovery_source=discovery_source)
    session.commit()

    return ep, work

def queue_assets_for_episode(session, episode_id: int, approval_mode=None):
    return plan_downloads(session, episode_id, approval_mode=approval_mode)
