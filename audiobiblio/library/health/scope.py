"""What the health detectors care about: book/drama programs, and which
programs a crawl target feeds. Read-only, grouped queries only (a
correlated subquery over episodes hung > 5 min in production)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.core.db.models import (
    ApprovalMode, CrawlTarget, Episode, Program, Series, Work,
)

# Unaccented, lowercased name fragments of literature / drama programs.
# News, talk and music programs are deliberately outside this scope.
_BOOK_NAME_RE = re.compile(
    r"cetb|cteni|povid|poctenicko|audiokn|radiokn|knih|divadl|drama|\bhra\b|\bhry\b"
    r"|literar|literatur|pohad|radioserial|osudy")


def norm_name(name: str | None) -> str:
    """'Cetba na pokracovani (CRo2) — stanice' → 'cetba na pokracovani'."""
    n = unidecode(name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.split(r"\s[-—–]+\s", n)[0]
    return " ".join(re.sub(r"[^a-z0-9]+", " ", n).split())


def is_book_name(name: str | None) -> bool:
    return bool(_BOOK_NAME_RE.search(unidecode(name or "").lower()))


@dataclass(frozen=True)
class ProgramStats:
    episodes: int
    newest: datetime | None  # newest episode created_at


def program_stats(session: Session) -> dict[int, ProgramStats]:
    rows = (session.query(Series.program_id, func.count(Episode.id),
                          func.max(Episode.created_at))
            .join(Work, Work.series_id == Series.id)
            .join(Episode, Episode.work_id == Work.id)
            .group_by(Series.program_id).all())
    return {pid: ProgramStats(n, newest) for pid, n, newest in rows}


def target_programs(session: Session, targets) -> dict[int, list[int]]:
    """target id → program ids it feeds: program url == target/paired url,
    else the unaccented name without '(CODE)'/'— stanice' suffixes."""
    by_url: dict[str, list[int]] = {}
    by_name: dict[str, list[int]] = {}
    for pid, name, url in session.query(Program.id, Program.name, Program.url):
        if url:
            by_url.setdefault(url.rstrip("/"), []).append(pid)
        by_name.setdefault(norm_name(name), []).append(pid)
    out: dict[int, list[int]] = {}
    for t in targets:
        pids = [p for u in (t.url, t.paired_url) if u for p in by_url.get(u.rstrip("/"), [])]
        if not pids and t.name:
            pids = by_name.get(norm_name(t.name), [])
        out[t.id] = pids
    return out


def active_targets(session: Session) -> list[CrawlTarget]:
    return session.query(CrawlTarget).filter(CrawlTarget.active.is_(True)).all()


def book_program_ids(session: Session) -> set[int]:
    """Programs whose not-downloaded episodes matter: book/drama names, the
    librarian's shelf programs, and programs fed by active AUTO targets."""
    from audiobiblio.library.pipelines.auto_finalize import DESTINATIONS, _norm
    ids = {pid for pid, name in session.query(Program.id, Program.name)
           if is_book_name(name) or _norm(name) in DESTINATIONS}
    auto = [t for t in active_targets(session) if t.approval_mode == ApprovalMode.AUTO]
    for pids in target_programs(session, auto).values():
        ids.update(pids)
    return ids
