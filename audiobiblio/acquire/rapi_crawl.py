"""rapi_crawl — rAPI-first crawl for REVIEW program targets.

The yt-dlp/HTML crawl costs 1–3 minutes per target; with ~1,300 targets
on a 24 h interval it fell ~10 days behind (2026-10-08: 1,144 targets
overdue). mujrozhlas show pages are client-rendered, so the HTML layer is
blind anyway. This path resolves the show UUID with one request and reads
the newest episodes straight from api.mujrozhlas.cz — a routine daily
check of a known show is a single request.

Identity: our ext_id is the rAPI legacy `meta.ga.contentId` (the same id
yt-dlp reports), so episodes found here and by the slow crawl dedupe.
URL-only legacy episodes are linked by show slug + normalized title.

AUTO (book) targets keep the full crawl: their per-book work grouping
feeds the finishing pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timezone

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.core.db.models import (
    AvailabilityStatus, CrawlTarget, Episode, EpisodeAlias, Program, Series, Work,
)
from audiobiblio.core.time import utcnow
from audiobiblio.library.pipelines.ingest import (
    _norm_program_name, clean_episode_title, upsert_from_item,
)
from audiobiblio.sources.discovery import normalize_rozhlas_url
from audiobiblio.sources.rapi import (
    _strip_html, best_audio_url, fetch_recent_episodes, resolve_show_uuid,
)

log = structlog.get_logger()

DISCOVERY_SOURCE = "rapi-crawl"
_MRZ_SLUG_RE = re.compile(r"^https?://(?:www\.)?mujrozhlas\.cz/([^/?#]+)/?$")


@dataclass
class RapiCrawlStats:
    show_uuid: str
    new: int = 0
    linked: int = 0
    errors: int = 0


def _content_id(e: dict) -> str | None:
    cid = ((e.get("meta") or {}).get("ga") or {}).get("contentId")
    return str(cid) if cid else None


def _ext_id(e: dict) -> str:
    """Our canonical ext_id: legacy contentId when present, else the UUID."""
    return _content_id(e) or e["id"]


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", unidecode(t or "")).strip().lower()


def _show_prefix(target: CrawlTarget) -> str | None:
    """'https://www.mujrozhlas.cz/<slug>/' for the target or its pair."""
    for url in (target.url, target.paired_url):
        if not url:
            continue
        m = _MRZ_SLUG_RE.match(normalize_rozhlas_url(url.split("?")[0]))
        if m:
            return f"https://www.mujrozhlas.cz/{m.group(1)}/"
    return None


def _parse_since(e: dict) -> datetime | None:
    since = (e.get("attributes") or {}).get("since")
    try:
        dt = datetime.fromisoformat(since) if since else None
    except ValueError:
        return None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC
    return dt


# Fallback program name upsert_from_item uses when nothing better is known —
# a catch-all bucket (9k+ episodes of many shows), never a placement target.
_CATCH_ALL_PROGRAMS = {"mujrozhlas"}


def _is_catch_all(program: Program) -> bool:
    return _norm_program_name(program.name) in _CATCH_ALL_PROGRAMS


class _ShowIndex:
    """What the DB already knows about one show — loaded once per crawl."""

    def __init__(self, s: Session, target: CrawlTarget):
        self.s = s
        self.prefix = _show_prefix(target)
        self.legacy: dict[str, Episode] = {}
        self.program: Program | None = None
        self.default_work: Work | None = None
        if self.prefix:
            in_show = Episode.url.like(self.prefix + "%")
            latest = s.query(Episode).filter(in_show).order_by(Episode.id.desc()).first()
            if latest is not None and not _is_catch_all(latest.work.series.program):
                self.default_work = latest.work
                self.program = latest.work.series.program
            # URL-only legacy rows: link by title only when the title is
            # unique within the show ("1. díl" style repeats never link).
            rows = s.query(Episode).filter(in_show, Episode.ext_id.is_(None)).all()
            counts = Counter(_norm_title(ep.title) for ep in rows)
            self.legacy = {_norm_title(ep.title): ep for ep in rows
                           if counts[_norm_title(ep.title)] == 1}
        if self.program is None and target.name:
            norm = _norm_program_name(target.name)
            hits = [p for p in s.query(Program).all()
                    if _norm_program_name(p.name) == norm and not _is_catch_all(p)]
            # same name on several stations → let the regular ingest decide
            self.program = hits[0] if len(hits) == 1 else None

    def is_known(self, e: dict) -> bool:
        ids = {e["id"], _content_id(e)} - {None}
        return (self.s.query(Episode.id).filter(Episode.ext_id.in_(ids)).first() is not None
                or self.s.query(EpisodeAlias.id).filter(
                    EpisodeAlias.ext_id.in_(ids)).first() is not None)

    def work_for(self, e: dict, show_title: str) -> Work:
        """Serial parts → the serial's work; everything else → the show's
        usual work (or one named after the show)."""
        attrs = e.get("attributes") or {}
        serial = attrs.get("mirroredSerial") or {}
        if not serial.get("title") and self.default_work is not None:
            return self.default_work
        title = serial.get("title") or show_title
        series = (self.s.query(Series).filter_by(program_id=self.program.id)
                  .order_by(Series.id).first())
        if series is None:
            series = Series(program_id=self.program.id, name=self.program.name)
            self.s.add(series)
            self.s.flush()
        work = self.s.query(Work).filter_by(series_id=series.id, title=title).first()
        if work is None:
            work = Work(series_id=series.id, title=title,
                        expected_total=serial.get("totalParts"),
                        expected_source="rapi" if serial.get("totalParts") else None)
            self.s.add(work)
            self.s.flush()
        if self.default_work is None and not serial.get("title"):
            self.default_work = work
        return work


def _touch(ep: Episode) -> None:
    now = utcnow()
    ep.first_seen_at = ep.first_seen_at or now
    ep.last_seen_at = now
    ep.last_checked_at = now
    ep.availability_status = AvailabilityStatus.AVAILABLE


def _create_episode(s: Session, idx: _ShowIndex, target: CrawlTarget, e: dict) -> Episode:
    attrs = e.get("attributes") or {}
    links = attrs.get("audioLinks") or []
    url = best_audio_url(links)
    duration = next((l.get("duration") for l in links if l.get("duration")), None)
    show_title = (attrs.get("mirroredShow") or {}).get("title") or target.name or ""
    common = dict(
        ext_id=_ext_id(e), episode_number=attrs.get("part"),
        summary=_strip_html(attrs.get("description")), published_at=_parse_since(e),
        duration_ms=int(duration) * 1000 if duration else None,
    )
    if idx.program is None:
        # Unknown show: the regular ingest builds station/program/series/work.
        ep, work = upsert_from_item(
            s, url=url, item_title=attrs.get("title") or "", series_name=None,
            author=None, uploader=None, program_name=target.name or show_title,
            program_url=target.url, source_url=target.url,
            work_title=(attrs.get("mirroredSerial") or {}).get("title") or show_title,
            discovery_source=DISCOVERY_SOURCE, **common)
        idx.program = work.series.program
        idx.default_work = idx.default_work or work
        return ep
    work = idx.work_for(e, show_title)
    ep = Episode(work_id=work.id, url=url, discovery_source=DISCOVERY_SOURCE,
                 title=clean_episode_title(attrs.get("title"), work.title, work.author)
                 or attrs.get("title") or e["id"], **common)
    s.add(ep)
    s.flush()
    return ep


def _ingest_one(s: Session, idx: _ShowIndex, target: CrawlTarget, e: dict,
                stats: RapiCrawlStats) -> None:
    legacy = idx.legacy.pop(_norm_title((e.get("attributes") or {}).get("title")), None)
    if legacy is not None:
        legacy.ext_id = _ext_id(e)
        _touch(legacy)
        stats.linked += 1
        return
    ep = _create_episode(s, idx, target, e)
    _touch(ep)
    # Index only — no DownloadJob (user decision 2026-10-08): REVIEW shows
    # must not flood the approval queue; downloading is decided per program.
    stats.new += 1


def crawl_target_via_rapi(s: Session, target: CrawlTarget) -> RapiCrawlStats | None:
    """Ingest the target's newest live episodes from rAPI.

    Returns None when the show cannot be resolved — the caller then falls
    back to the full yt-dlp/HTML crawl.
    """
    uuid = resolve_show_uuid([target.url, target.paired_url], name=target.name)
    if not uuid:
        return None
    idx = _ShowIndex(s, target)
    stats = RapiCrawlStats(show_uuid=uuid)
    fresh = fetch_recent_episodes(uuid, is_known=idx.is_known)
    seen: set[str] = set()
    for e in reversed(fresh):  # oldest first — stable numbering/order
        ext = _ext_id(e)
        if ext in seen:  # re-published item sharing a contentId
            continue
        seen.add(ext)
        try:
            _ingest_one(s, idx, target, e, stats)
            s.commit()
        except IntegrityError as ex:
            # one bad row must not sink the whole show (and push it onto
            # the slow path every cycle)
            s.rollback()
            stats.errors += 1
            log.warning("rapi_crawl_episode_failed", url=target.url, ext_id=ext,
                        error=str(ex.orig))
    log.info("rapi_crawl_done", url=target.url, show=uuid, new=stats.new,
             linked=stats.linked, errors=stats.errors)
    return stats
