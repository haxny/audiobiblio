"""Independent verifier: does a book hold every part rAPI lists for its serial?

Every earlier fix lived on ONE ingest path (crawler, show-sweep, serial-sweep,
rapi-crawl) and nothing checked the RESULT, so each new variant surfaced only
when the user noticed a missing part (Hrabal, Prosecké peklo, Doležal part 2,
2026-11). This compares the end state with the source, whatever built the book.

Report-only by design: it reads rAPI and the DB and never writes (a serial UUID
found through an episode is reported as `discovered`, not stamped). Fixing is a
separate, explicit step (`audiobiblio verify-serials --fix`).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from audiobiblio.acquire.serial_sweep import REQUEST_GAP_S, _API, _http
from audiobiblio.core.db.models import Episode, EpisodeAlias, Work

log = structlog.get_logger()

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PAGE_LIMIT = 100


def _default_fetch(url: str) -> dict:
    return json.loads(_http(url).read())


@dataclass(frozen=True)
class BookCheck:
    work_id: int
    title: str
    serial_uuid: str | None
    have: tuple[int, ...] = ()
    rapi_total: int = 0
    missing_live: tuple[int, ...] = ()       # downloadable now
    missing_expired: tuple[int, ...] = ()    # listed, but no audio link any more
    held_elsewhere: tuple[int, ...] = ()     # missing here, owned by another book
    held_in: tuple[int, ...] = ()            # ... the work ids of those books (split signature)
    discovered: bool = False                 # serial found via an episode, not stamped
    truncated: bool = False                  # rAPI page was full: may be incomplete
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None and not self.missing_live and not self.missing_expired

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass
class VerifyReport:
    checked: int = 0
    skipped: int = 0
    incomplete: list[BookCheck] = field(default_factory=list)
    errors: list[BookCheck] = field(default_factory=list)

    @property
    def missing_live(self) -> int:
        return sum(len(c.missing_live) for c in self.incomplete)

    def as_dict(self) -> dict:
        return {"checked": self.checked, "skipped": self.skipped,
                "missing_live": self.missing_live,
                "incomplete": [c.as_dict() for c in self.incomplete],
                "errors": [c.as_dict() for c in self.errors]}


def _content_id(e: dict) -> str | None:
    cid = ((e.get("meta") or {}).get("ga") or {}).get("contentId")
    return str(cid) if cid else None


def _book_ids(session: Session, work: Work) -> set[str]:
    ids = {e.ext_id for e in work.episodes if e.ext_id}
    ep_ids = [e.id for e in work.episodes]
    if ep_ids:
        ids |= {a for (a,) in session.query(EpisodeAlias.ext_id).filter(
            EpisodeAlias.episode_id.in_(ep_ids), EpisodeAlias.ext_id.isnot(None))}
    return ids


def _uuid_candidates(session: Session, work: Work) -> list[str]:
    out = [i for i in _book_ids(session, work) if _UUID_RE.match(i)]
    return sorted(out)[:3]


def _discover_serial(session: Session, work: Work, fetch: Callable[[str], dict]) -> str | None:
    for uid in _uuid_candidates(session, work):
        try:
            data = (fetch(f"{_API}/episodes/{uid}").get("data") or {})
        except urllib.error.HTTPError as exc:
            if exc.code == 404:      # not an rAPI episode (any more): not a serial book
                continue
            raise
        serial = (((data.get("relationships") or {}).get("serial") or {}).get("data") or {}).get("id")
        if serial:
            return serial
    return None


def _holders(session: Session, keys: set[str], work_id: int) -> set[int]:
    """Other works that hold a recording by any of these ids."""
    if not keys:
        return set()
    out = {w for (w,) in session.query(Episode.work_id).filter(
        Episode.ext_id.in_(keys), Episode.work_id != work_id)}
    out |= {w for (w,) in session.query(Episode.work_id).join(
        EpisodeAlias, EpisodeAlias.episode_id == Episode.id).filter(
        EpisodeAlias.ext_id.in_(keys), Episode.work_id != work_id)}
    return out


def verify_book(session: Session, work: Work,
                fetch: Callable[[str], dict] = _default_fetch) -> BookCheck | None:
    """Compare one book with its rAPI serial. None = not a serial book we can
    identify (no stamped UUID and no episode carrying an rAPI UUID)."""
    have = tuple(sorted({e.episode_number for e in work.episodes if e.episode_number is not None}))
    try:
        serial, discovered = work.serial_uuid, False
        if not serial:
            serial, discovered = _discover_serial(session, work, fetch), True
            if not serial:
                return None
        data = fetch(f"{_API}/serials/{serial}/episodes?page%5Blimit%5D={_PAGE_LIMIT}")
        eps = data.get("data") or []
    except Exception as exc:  # network / API trouble is a finding, not a crash
        return BookCheck(work.id, work.title, work.serial_uuid, have=have,
                         error=f"{type(exc).__name__}: {exc}")

    ids = _book_ids(session, work)
    live, expired, elsewhere = [], [], []
    holders: set[int] = set()
    total = 0
    for e in eps:
        a = e.get("attributes") or {}
        total = max(total, int((a.get("mirroredSerial") or {}).get("totalParts") or 0))
        part = a.get("part")
        keys = {k for k in (e.get("id"), _content_id(e)) if k}
        if keys & ids or (part is not None and part in have):
            continue
        (live if a.get("audioLinks") else expired).append(part)
        found = _holders(session, keys, work.id)
        if found:
            elsewhere.append(part)
            holders |= found
    return BookCheck(
        work.id, work.title, serial, have=have, rapi_total=max(total, len(eps)),
        missing_live=tuple(sorted(p for p in live if p is not None)),
        missing_expired=tuple(sorted(p for p in expired if p is not None)),
        held_elsewhere=tuple(sorted(p for p in elsewhere if p is not None)),
        held_in=tuple(sorted(holders)),
        discovered=discovered, truncated=len(eps) >= _PAGE_LIMIT)


def verify_all(session: Session, fetch: Callable[[str], dict] = _default_fetch,
               limit: int = 200, sleep: Callable[[float], None] = time.sleep) -> VerifyReport:
    """Verify up to *limit* serial books (stamped ones first, newest first).
    Books whose serial cannot be identified are skipped, not counted."""
    uuid_works = (session.query(Work.id)
                  .join(Episode, Episode.work_id == Work.id)
                  .filter(func.length(Episode.ext_id) == 36)
                  .group_by(Work.id)
                  # stamped books, books with several rAPI-keyed parts or a known
                  # total; a lone news episode is not worth a request
                  .having(or_(Work.serial_uuid.isnot(None), func.count(Episode.id) >= 2,
                              func.coalesce(Work.expected_total, 0) > 1))
                  .order_by((Work.serial_uuid.is_(None)).asc(), Work.id.desc())
                  .limit(limit * 3).all())
    rep = VerifyReport()
    for (wid,) in uuid_works:
        if rep.checked >= limit:
            break
        chk = verify_book(session, session.get(Work, wid), fetch)
        sleep(REQUEST_GAP_S)
        if chk is None:
            rep.skipped += 1
            continue
        rep.checked += 1
        if chk.error:
            rep.errors.append(chk)
        elif not chk.complete:
            rep.incomplete.append(chk)
    log.info("serial_verify_done", checked=rep.checked, skipped=rep.skipped,
             incomplete=len(rep.incomplete), errors=len(rep.errors),
             missing_live=rep.missing_live)
    return rep
