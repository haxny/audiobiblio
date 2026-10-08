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
from audiobiblio.acquire import rapi_backfill_state

log = structlog.get_logger()

DISCOVERY_SOURCE = "rapi-crawl"
HEAD_PAGES = 3        # newest-first pages per crawl (stops early when idle);
                      # 300 req/h budget: 1,349 shows × 3 ≈ 14 h first pass
BACKFILL_PAGES = 2    # deeper-history pages per crawl until exhausted
PAGE_SIZE = 100
BACKFILL_OVERLAP = 20  # new episodes shift offsets; re-read a margin
_MRZ_SLUG_RE = re.compile(r"^https?://(?:www\.)?mujrozhlas\.cz/([^/?#]+)/?$")


@dataclass
class RapiCrawlStats:
    show_uuid: str
    new: int = 0       # live episodes indexed
    revived: int = 0   # our GONE episodes live again (re-air)
    linked: int = 0
    errors: int = 0


def _content_id(e: dict) -> str | None:
    cid = ((e.get("meta") or {}).get("ga") or {}).get("contentId")
    return str(cid) if cid else None


def _ext_id(e: dict) -> str:
    """Our canonical ext_id: legacy contentId when present, else the UUID."""
    return _content_id(e) or e["id"]


def _is_live(e: dict) -> bool:
    return bool((e.get("attributes") or {}).get("audioLinks"))


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

    def lookup(self, e: dict) -> Episode | None:
        ids = {e["id"], _content_id(e)} - {None}
        ep = self.s.query(Episode).filter(Episode.ext_id.in_(ids)).first()
        if ep is None:
            alias = self.s.query(EpisodeAlias).filter(EpisodeAlias.ext_id.in_(ids)).first()
            ep = self.s.get(Episode, alias.episode_id) if alias else None
        return ep

    def needs_action(self, e: dict) -> bool:
        """Live episode we don't have, or our GONE episode live again.
        (Show listings carry live episodes only — verified 2026-10-08.)"""
        if not _is_live(e):
            return False
        ep = self.lookup(e)
        return ep is None or ep.availability_status == AvailabilityStatus.GONE

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
    known = idx.lookup(e)
    if known is not None:  # needs_action let it through → a re-air revival
        known.url = best_audio_url(e["attributes"]["audioLinks"])
        _touch(known)
        stats.revived += 1
        log.info("rapi_crawl_revived", episode_id=known.id, ext_id=known.ext_id)
        return
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


def _walk(uuid: str, idx: _ShowIndex) -> list[dict]:
    """Head walk (newest pages, stops when idle) plus — once per show, until
    its whole live history has been read — a few deeper pages per crawl.
    A show already indexed at the top still gets its older live episodes."""
    state = rapi_backfill_state.load()
    head = fetch_recent_episodes(uuid, idx.needs_action, page_size=PAGE_SIZE,
                                 max_pages=HEAD_PAGES)
    items = list(head.items)
    cursor = state.get(uuid)
    if head.exhausted:
        state[uuid] = rapi_backfill_state.DONE  # whole history fit the head walk
    elif cursor != rapi_backfill_state.DONE:
        start = head.next_offset if cursor is None else max(0, cursor - BACKFILL_OVERLAP)
        deep = fetch_recent_episodes(uuid, idx.needs_action, page_size=PAGE_SIZE,
                                     max_pages=BACKFILL_PAGES, start_offset=start,
                                     stop_when_idle=False)
        items.extend(deep.items)
        state[uuid] = rapi_backfill_state.DONE if deep.exhausted else deep.next_offset
        if deep.exhausted:
            log.info("rapi_backfill_done", show=uuid)
    rapi_backfill_state.save(state)
    return items


def crawl_target_via_rapi(s: Session, target: CrawlTarget) -> RapiCrawlStats | None:
    """Index the target's live episodes from rAPI (new ones, and our GONE
    episodes that are live again after a re-air).

    Returns None when the show cannot be resolved — the caller then falls
    back to the full yt-dlp/HTML crawl.
    """
    uuid = resolve_show_uuid([target.url, target.paired_url], name=target.name)
    if not uuid:
        return None
    idx = _ShowIndex(s, target)
    stats = RapiCrawlStats(show_uuid=uuid)
    items = sorted(_walk(uuid, idx),
                   key=lambda e: (e.get("attributes") or {}).get("since") or "")
    seen: set[str] = set()
    for e in items:  # oldest first — stable numbering/order
        ext = _ext_id(e)
        if ext in seen:  # re-published item sharing a contentId / head∩backfill
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
             revived=stats.revived, linked=stats.linked,
             errors=stats.errors)
    return stats
