"""archive_history — aired history from station archives.

rAPI show listings carry live episodes only, so the REVIEW crawl (rAPI-first)
never sees what already expired. The station archive (<sub>.rozhlas.cz
program page, ?page=N) lists every aired episode with air date and
annotation. This module walks it WEEKLY for REVIEW targets — history only:
no yt-dlp probes, no download jobs.

rAPI exposes no page URL, so a card cannot be matched to its live episode
by URL. Conservative rules, all scoped to the target's own program:
  - same broadcast (same air day ±1, title equal/near) → alias, no new row
  - the program has ANY live episode that day → skip (live = rAPI's job)
  - card younger than RECENT_DAYS → skip (audio still live; rAPI may lag)
  - otherwise → GONE stub (expired aired history)

The helpers are shared with the crawler's daily walk for AUTO targets.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import structlog
from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.acquire.rapi_backfill_state import load_json, save_json
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, AvailabilityStatus, CrawlTarget, Episode, Program,
    Series, Work,
)
from audiobiblio.core.time import utcnow
from audiobiblio.core.urls import norm_url as _norm_url
from audiobiblio.library.pipelines.ingest import (
    _add_alias, _norm_program_name, upsert_from_item,
)
from audiobiblio.sources.rozhlas_station import (
    fetch_archive_stubs, is_station_program_url,
)

log = structlog.get_logger()

STATE_FILE = "archive_walk.json"
INTERVAL = timedelta(days=7)
SPREAD_DAYS = 7  # first walks are spread by target.id over the week
RECENT_DAYS = 14  # younger cards are live — indexed by the rAPI crawl
TITLE_SIMILARITY = 0.85
_CATCH_ALL = {"mujrozhlas"}


def _episode_for_stub(s, stub_url: str) -> Episode | None:
    """Episode already indexed under an archive article URL (url or alias)."""
    return (s.query(Episode).filter(Episode.url == stub_url).first()
            or s.query(Episode).join(Episode.aliases).filter_by(
                url=_norm_url(stub_url)).first())


def _stub_is_complete(ep: Episode | None, stub) -> bool:
    """Known AND nothing left to backfill from the card — only then may the
    archive walk treat the stub as done."""
    return ep is not None and bool(ep.published_at or not stub.published_at) \
        and bool(ep.summary or not stub.perex)


def _ingest_archive_stub(s, stub, program_name: str | None, program_url: str) -> None:
    """Index an aired episode whose audio is no longer online: air date +
    annotation, audio asset MISSING, availability GONE — NO download jobs
    (they would only error). A future re-air revives and downloads it."""
    ep, _work = upsert_from_item(
        s,
        url=stub.url,
        item_title=stub.title,
        series_name=program_name,
        author=None,
        uploader=None,
        work_title=stub.title,
        episode_number=1,
        program_name=program_name,
        program_url=program_url,
        source_url=stub.url,
        summary=stub.perex,
        published_at=stub.published_at,
    )
    ep.availability_status = AvailabilityStatus.GONE
    audio = s.query(Asset).filter_by(episode_id=ep.id, type=AssetType.AUDIO).first()
    if audio is None:
        s.add(Asset(episode_id=ep.id, type=AssetType.AUDIO,
                    status=AssetStatus.MISSING))
    s.commit()


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", unidecode(t or "")).strip().lower()


def program_ids(s: Session, target: CrawlTarget) -> set[int]:
    """The target's program(s): normalized-name match, catch-all excluded."""
    if not target.name:
        return set()
    norm = _norm_program_name(target.name)
    return {p.id for p in s.query(Program).all()
            if _norm_program_name(p.name) == norm and p.name.lower() not in _CATCH_ALL}


def _day_window(stub) -> tuple[datetime, datetime]:
    day = datetime(stub.published_at.year, stub.published_at.month, stub.published_at.day)
    return day - timedelta(days=1), day + timedelta(days=2)  # ±1 for UTC shift


def _program_episodes_that_day(s: Session, stub, prog_ids: set[int]) -> list[Episode]:
    start, end = _day_window(stub)
    return (s.query(Episode).join(Work, Episode.work_id == Work.id)
            .join(Series, Work.series_id == Series.id)
            .filter(Series.program_id.in_(prog_ids),
                    Episode.published_at >= start, Episode.published_at < end).all())


def _titles_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 8 and (a.startswith(b) or b.startswith(a))):
        return True
    return SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY


def _same_broadcast(day_eps: list[Episode], stub) -> Episode | None:
    """The program's episode of that day carrying the card's title — only an
    unambiguous single hit counts."""
    want = _norm_title(stub.title)
    hits = [ep for ep in day_eps if _titles_match(_norm_title(ep.title), want)]
    return hits[0] if len(hits) == 1 else None


def station_url(target: CrawlTarget) -> str | None:
    """The target's station-site program page (target or its pair)."""
    for url in (target.url, target.paired_url):
        if is_station_program_url(url):
            return url
    return None


def _last_walk(state: dict, target: CrawlTarget) -> tuple[datetime | None, bool]:
    """(when, completed) of the target's last walk; legacy plain timestamps
    count as completed."""
    raw = state.get(str(target.id))
    if raw is None:
        return None, False
    if isinstance(raw, str):
        raw = {"at": raw, "complete": True}
    try:
        return datetime.fromisoformat(raw.get("at")), bool(raw.get("complete"))
    except (TypeError, ValueError, AttributeError):
        return None, False


def is_due(target: CrawlTarget, state: dict, now: datetime) -> bool:
    at, complete = _last_walk(state, target)
    if at is None:
        return target.id % SPREAD_DAYS == now.weekday()
    if not complete:
        return True  # an interrupted walk is retried on the next crawl
    return now - at >= INTERVAL


def walk_archive_history(s: Session, target: CrawlTarget, url: str,
                         early_stop: bool = True) -> dict:
    """Index the archive's unknown, expired cards (see module rules).
    early_stop=False re-reads the whole archive (after an interrupted walk
    left page 0 'known' with older pages never reached). Raises when a page
    fails to load — the caller records the walk as incomplete."""
    stats = {"stubs": 0, "gone": 0, "aliased": 0, "backfilled": 0,
             "skipped_live": 0, "skipped_recent": 0, "skipped_undated": 0, "errors": 0}
    known = (lambda st: _stub_is_complete(_episode_for_stub(s, st.url), st)) \
        if early_stop else None
    stubs = fetch_archive_stubs(url, is_known=known, strict=True)
    stats["stubs"] = len(stubs)
    prog_ids = program_ids(s, target)
    recent_cutoff = utcnow() - timedelta(days=RECENT_DAYS)
    for stub in stubs:
        try:
            _walk_one(s, target, url, stub, prog_ids, recent_cutoff, stats)
            s.commit()
        except Exception as e:
            s.rollback()  # one poison card must not abort the walk
            stats["errors"] += 1
            log.warning("archive_walk_card_failed", url=stub.url, error=str(e))
    return stats


def _walk_one(s: Session, target: CrawlTarget, url: str, stub, prog_ids: set[int],
              recent_cutoff: datetime, stats: dict) -> None:
    existing = _episode_for_stub(s, stub.url)
    if existing is not None:
        if stub.published_at and not existing.published_at:
            existing.published_at = stub.published_at
        if stub.perex and not existing.summary:
            existing.summary = stub.perex
        stats["backfilled"] += 1
        return
    if not stub.published_at:
        stats["skipped_undated"] += 1  # cannot tell live from expired
        return
    day_eps = _program_episodes_that_day(s, stub, prog_ids) if prog_ids else []
    twin = _same_broadcast(day_eps, stub)
    if twin is not None:
        _add_alias(s, twin, stub.url, discovery_source="archive-walk")
        if stub.perex and not twin.summary:
            twin.summary = stub.perex
        stats["aliased"] += 1
        return
    if any(ep.availability_status == AvailabilityStatus.AVAILABLE for ep in day_eps):
        stats["skipped_live"] += 1
        return
    if stub.published_at >= recent_cutoff:
        stats["skipped_recent"] += 1
        return
    _ingest_archive_stub(s, stub, target.name, url)
    stats["gone"] += 1


def maybe_walk_archive(s: Session, target: CrawlTarget) -> dict | None:
    """Weekly archive walk for one REVIEW target; None when not due / no
    station page. A failed walk is recorded incomplete → retried next crawl
    as a full (no early-stop) walk."""
    url = station_url(target)
    if url is None:
        return None
    now = utcnow()
    state = load_json(STATE_FILE)
    if not is_due(target, state, now):
        return None
    at, complete = _last_walk(state, target)
    early_stop = at is None or complete
    try:
        stats = walk_archive_history(s, target, url, early_stop=early_stop)
    except Exception as e:
        s.rollback()
        state[str(target.id)] = {"at": now.isoformat(), "complete": False}
        save_json(STATE_FILE, state)
        log.warning("archive_walk_incomplete", url=url, error=str(e))
        return None
    state[str(target.id)] = {"at": now.isoformat(), "complete": True}
    save_json(STATE_FILE, state)
    log.info("archive_walk_done", url=url, **stats)
    return stats
