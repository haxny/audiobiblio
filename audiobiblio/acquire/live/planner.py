"""Daily look-ahead: schedule → record-worthy windows → live_recordings rows.

Idempotent: re-running updates still-planned rows in place, cancels slots
that disappeared from the schedule, never re-plans what is already
recorded, and keeps reruns as BACKUP airings of their primary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

import structlog
from sqlalchemy.orm import Session

from audiobiblio.acquire.live.detect import (
    DEFAULT_RULES, STATION_ALIASES, STATIONS, Window, detect, norm,
)
from audiobiblio.acquire.live.schedule import RapiClient, ScheduleItem, stream_urls
from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, Episode, LiveRecording, LiveStatus, Program, Series, Work,
)
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

_OPEN = (LiveStatus.PLANNED, LiveStatus.BACKUP)
MIN_TARGET_KEY = 4          # target names shorter than this match too much
_ACTIVE = (LiveStatus.PLANNED, LiveStatus.RECORDING, LiveStatus.FINALIZING)


class ScheduleSource(Protocol):
    def fetch_stations(self) -> list[dict]: ...
    def fetch_schedule(self, start, days: int) -> list[ScheduleItem]: ...


def _row_values(w: Window, stream: str) -> dict:
    spec = STATIONS.get(w.station_id)
    return dict(
        station_id=w.station_id, station_code=spec.code if spec else "CRo",
        kind=w.kind, dedupe_key=w.dedupe_key, title=w.title[:600],
        series_title=(w.series_title or w.title)[:600], part=w.part, total_parts=w.total,
        program_name=(w.program or None),
        schedule_start=_utc(w.schedule_start),
        schedule_end=_utc(w.schedule_end),
        window_start=w.window_start_utc, window_end=w.window_end_utc, stream_url=stream,
        items=[i.to_json() for i in w.items], description=(w.description or "")[:8000],
    )


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def plan_recordings(session: Session, windows: list[Window], streams: dict[str, str],
                    now: datetime | None = None,
                    horizon: tuple[datetime, datetime] | None = None) -> dict:
    """Persist *windows* (all airings). *now*/*horizon* are naive UTC."""
    now = now or utcnow()
    stats = {"new": 0, "updated": 0, "cancelled": 0, "skipped_done": 0, "past": 0, "backup": 0}
    keys = {w.dedupe_key for w in windows}
    rows = session.query(LiveRecording).filter(LiveRecording.dedupe_key.in_(keys)).all() if keys else []
    done = {r.dedupe_key for r in rows if r.status == LiveStatus.COMPLETE}
    active = {r.dedupe_key for r in rows if r.status in _ACTIVE}
    by_airing = {(r.dedupe_key, r.schedule_start): r for r in rows}
    seen: set[tuple[str, datetime]] = set()

    for w in sorted(windows, key=lambda w: w.schedule_start):
        start = _utc(w.schedule_start)
        seen.add((w.dedupe_key, start))
        if w.window_end_utc <= now:
            stats["past"] += 1
            continue
        if w.dedupe_key in done:
            stats["skipped_done"] += 1
            continue
        stream = streams.get(w.station_id) or (STATIONS[w.station_id].default_stream
                                               if w.station_id in STATIONS else "")
        values = _row_values(w, stream)
        existing = by_airing.get((w.dedupe_key, start))
        if existing is not None:
            if existing.status in _OPEN:
                for k in ("window_start", "window_end", "schedule_end", "stream_url", "items",
                          "title", "description", "program_name"):
                    setattr(existing, k, values[k])
                stats["updated"] += 1
            continue
        status = LiveStatus.BACKUP if w.dedupe_key in active else LiveStatus.PLANNED
        if status == LiveStatus.PLANNED:
            active.add(w.dedupe_key)
        else:
            stats["backup"] += 1
        session.add(LiveRecording(**values, status=status, runs=[]))
        stats["new"] += 1
        log.info("live_planned", title=w.title, station=values["station_code"],
                 start=str(values["window_start"]), end=str(values["window_end"]),
                 status=status.value, kind=w.kind)

    if horizon is not None:
        stats["cancelled"] = _cancel_vanished(session, seen, now, horizon)
    session.commit()
    return stats


def _cancel_vanished(session: Session, seen: set, now: datetime,
                     horizon: tuple[datetime, datetime]) -> int:
    lo, hi = max(horizon[0], now), horizon[1]
    gone = [r for r in session.query(LiveRecording).filter(
        LiveRecording.status.in_(_OPEN), LiveRecording.schedule_start >= lo,
        LiveRecording.schedule_start < hi).all()
        if (r.dedupe_key, r.schedule_start) not in seen]
    for r in gone:
        was_primary = r.status == LiveStatus.PLANNED
        r.status = LiveStatus.CANCELLED
        r.error = "slot no longer in the schedule"
        log.info("live_cancelled", id=r.id, title=r.title, start=str(r.schedule_start))
        if was_primary:
            promote_backup(session, r.dedupe_key, now)
    return len(gone)


def promote_backup(session: Session, dedupe_key: str, now: datetime) -> LiveRecording | None:
    """Next future BACKUP airing of *dedupe_key* becomes PLANNED."""
    nxt = session.query(LiveRecording).filter(
        LiveRecording.dedupe_key == dedupe_key, LiveRecording.status == LiveStatus.BACKUP,
        LiveRecording.window_start > now).order_by(LiveRecording.window_start).first()
    if nxt is not None:
        nxt.status = LiveStatus.PLANNED
        log.info("live_backup_promoted", id=nxt.id, title=nxt.title, start=str(nxt.window_start))
    return nxt


def available_elsewhere(session: Session) -> Callable[[str, int], bool]:
    """(series, part) → True when the part is permanently available another
    way, so recording it live would only duplicate:

    * the library already has that part — episode number matches and the
      work title, its program or the episode title names the series
      (production: program 'Urgent', work 'Urgent. Poslouchejte…',
      episode '#2 Vyhlaseni traumaplanu');
    * an active AUTO crawl target covers the series by name ('BáSnění'
      for 'BáSnění - Anna Gažiová'). REVIEW targets do not count: e.g.
      'Slovo o hudbě' is a 2017 archive show, the new D-dur parts are not
      in it."""
    auto = [n for (n,) in session.query(CrawlTarget.name).filter(
        CrawlTarget.active.is_(True), CrawlTarget.approval_mode == ApprovalMode.AUTO).all()]

    def episodes(part: int) -> list[tuple[str, str, str]]:
        return session.query(Work.title, Program.name, Episode.title) \
            .join(Episode, Episode.work_id == Work.id) \
            .join(Series, Series.id == Work.series_id) \
            .join(Program, Program.id == Series.program_id) \
            .filter(Episode.episode_number == part).all()
    return make_known(auto, episodes)


def make_known(auto_target_names: list[str],
               episodes: Callable[[int], list[tuple[str, str, str]]]) -> Callable[[str, int], bool]:
    """Pure matcher behind available_elsewhere(). *episodes(part)* returns
    (work title, program name, episode title) rows with that part number."""
    auto = [k for k in (norm(n) for n in auto_target_names if n) if len(k) >= MIN_TARGET_KEY]
    cache: dict[int, list[tuple[str, str, str]]] = {}

    def _starts(text: str, key: str) -> bool:
        return text == key or text.startswith(key + " ")

    def known(series: str, part: int) -> bool:
        key = norm(series)
        if not key:
            return False
        if any(_starts(key, t) for t in auto):
            return True
        if part not in cache:
            cache[part] = [(norm(wt), norm(pn), norm(et)) for wt, pn, et in episodes(part)]
        return any(_starts(wt, key) or pn == key or et.startswith(key)
                   for wt, pn, et in cache[part])
    return known


def stations_from_config(names: list[str]) -> dict[str, tuple[str, ...]]:
    out = {}
    for name in names:
        sid = STATION_ALIASES.get(str(name).strip().lower())
        if sid is None:
            log.warning("live_unknown_station", station=name)
            continue
        out[sid] = DEFAULT_RULES[sid]
    return out


def run_lookahead(session: Session, client: ScheduleSource | None = None,
                  now: datetime | None = None, stations: dict[str, tuple[str, ...]] | None = None,
                  days: int | None = None) -> dict:
    if stations is None or days is None:
        from audiobiblio.core.config import load_config
        cfg = load_config()
        stations = stations if stations is not None else stations_from_config(cfg.live_stations)
        days = days if days is not None else int(cfg.live_lookahead_days)
    now = now or utcnow()
    client = client or RapiClient()
    try:
        streams = stream_urls(client.fetch_stations())
    except Exception as e:  # defaults in STATIONS cover the configured ones
        log.warning("live_stations_fetch_failed", error=str(e))
        streams = {}
    # From yesterday: the Saturday block must still be visible on Sunday,
    # or its Sunday-night rerun (the backup) would look vanished.
    start = (now + timedelta(hours=1)).date() - timedelta(days=1)
    items = client.fetch_schedule(start, days + 1)
    windows = detect(items, stations, known=available_elsewhere(session))
    stats = plan_recordings(session, windows, streams, now=now,
                            horizon=(now, now + timedelta(days=days)))
    log.info("live_lookahead_done", items=len(items), windows=len(windows), **stats)
    return stats
