"""Download priority policy — Episode.priority from content class + expiry.

The download worker takes PENDING jobs by Episode.priority DESC, then job
id. Owner policy (2026-11-07):

  rank 1  books / readings / dramas expiring soon        80 (≤7 days: 90)
  rank 2  serials / music cycles expiring soon            50
          ordinary book / drama / unclassified downloads  10
          music-other, news/talk, no target known          0
  rank 3  serials / music cycles NOT expiring            -10

"Expiring soon" = rAPI `audioLinks[].playableTill` within
EXPIRY_WINDOW_DAYS. A link that exists at all is NOT enough: rAPI uses
far-future placeholders (2031-12-31) for most "permanent" audio.

Unclassified targets (content_class NULL) are the legacy AUTO book targets:
they escalate like books. A priority is only ever raised, never lowered —
manual and older boosts (serial sweep 90, gap-fill 10) survive.

Priorities are computed at creation; `bump_expiring` re-escalates pending
episodes whose playable_till has since entered the window (run after every
crawl cycle — pure DB, cheap).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import CrawlTarget, DownloadJob, Episode, JobStatus
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

BOOK, DRAMA, SERIAL = "BOOK", "DRAMA", "SERIAL"
MUSIC_CYCLE, MUSIC_OTHER, NEWS_TALK = "MUSIC-CYCLE", "MUSIC-OTHER", "NEWS/TALK"
CONTENT_CLASSES = (BOOK, DRAMA, SERIAL, MUSIC_CYCLE, MUSIC_OTHER, NEWS_TALK)

EXPIRY_WINDOW_DAYS = 30
URGENT_DAYS = 7

BOOK_URGENT = 90      # rank 1, last week
BOOK_EXPIRING = 80    # rank 1
SERIAL_EXPIRING = 50  # rank 2
ORDINARY_BOOK = 10
DEFAULT = 0
SERIAL_BASE = -10     # rank 3

_BOOKISH = {BOOK, DRAMA, None}        # None = legacy AUTO (book) target
_SERIALISH = {SERIAL, MUSIC_CYCLE}
_BASE = {BOOK: ORDINARY_BOOK, DRAMA: ORDINARY_BOOK, None: ORDINARY_BOOK,
         SERIAL: SERIAL_BASE, MUSIC_CYCLE: SERIAL_BASE}


def _days_left(playable_till: datetime | None, now: datetime) -> float | None:
    if playable_till is None or playable_till <= now:
        return None  # unknown, or already gone — escalation cannot help
    return (playable_till - now).total_seconds() / 86400


def _expiry_level(content_class: str | None, days: float | None) -> int | None:
    if days is None or days > EXPIRY_WINDOW_DAYS:
        return None
    if content_class in _BOOKISH:
        return BOOK_URGENT if days <= URGENT_DAYS else BOOK_EXPIRING
    if content_class in _SERIALISH:
        return SERIAL_EXPIRING
    return None  # music-other / news-talk never escalate


def compute_priority(content_class: str | None, playable_till: datetime | None,
                     priority_base: int | None = None,
                     now: datetime | None = None) -> int:
    """Priority for a new episode of a target of *content_class*."""
    now = now or utcnow()
    base = priority_base if priority_base is not None else _BASE.get(content_class, DEFAULT)
    level = _expiry_level(content_class, _days_left(playable_till, now))
    return base if level is None else max(base, level)


def target_priority(target: CrawlTarget | None, playable_till: datetime | None,
                    now: datetime | None = None) -> int:
    if target is None:
        return compute_priority(None, playable_till, now=now) if playable_till else DEFAULT
    return compute_priority(target.content_class, playable_till,
                            priority_base=target.priority_base, now=now)


def parse_playable_till(e: dict) -> datetime | None:
    """Earliest `audioLinks[].playableTill` of an rAPI episode, naive UTC."""
    from audiobiblio.sources.rapi import playable_till
    return playable_till((e.get("attributes") or {}).get("audioLinks"))


def raise_priority(ep: Episode, prio: int) -> bool:
    """Raise ep.priority to *prio*; never lower. True when changed."""
    if prio > (ep.priority or 0):
        ep.priority = prio
        return True
    return False


def apply_new_episode_priority(ep: Episode, prio: int) -> None:
    """For an episode created just now: take *prio* even when negative,
    unless something already set a higher value (gap-fill, caller)."""
    current = ep.priority
    if current is None or current == DEFAULT or prio > current:
        ep.priority = prio


def _bump_target(priority: int, days: float) -> int | None:
    """Escalation for an already-prioritised episode, inferred from its
    band: negative = demoted serial/music (rank 3 → rank 2); the serial
    band [50, 80) stays; everything else escalates like a book."""
    if priority < 0:
        return SERIAL_EXPIRING
    if SERIAL_EXPIRING <= priority < BOOK_EXPIRING:
        return None
    return BOOK_URGENT if days <= URGENT_DAYS else BOOK_EXPIRING


def bump_expiring(session: Session, now: datetime | None = None) -> int:
    """Raise PENDING episodes whose playable_till entered the expiry window.
    Returns the number of episodes changed. Never lowers; idempotent."""
    now = now or utcnow()
    horizon = now + timedelta(days=EXPIRY_WINDOW_DAYS)
    pending = select(DownloadJob.episode_id).where(DownloadJob.status == JobStatus.PENDING)
    rows = session.query(Episode).filter(
        Episode.playable_till.isnot(None),
        Episode.playable_till > now,
        Episode.playable_till <= horizon,
        Episode.id.in_(pending),
    ).all()
    changed = 0
    for ep in rows:
        target = _bump_target(ep.priority or 0, _days_left(ep.playable_till, now))
        if target is not None and raise_priority(ep, target):
            changed += 1
    if changed:
        session.commit()
        log.info("priority_bump_expiring", episodes=changed)
    return changed
