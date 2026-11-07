"""Crawl-side detectors: targets that stay silent, targets that keep failing."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import ApprovalMode, CrawlRun, CrawlTarget
from audiobiblio.core.time import utcnow
from audiobiblio.library.health.findings import Finding, error_signature, link
from audiobiblio.library.health.scope import (
    active_targets, is_book_name, program_stats, target_programs,
)

STALL_DAYS = 30        # AUTO target: no new episode for this long
STALL_RUNS = 5         # ...and (when recorded) its last N runs brought nothing
MIN_CRAWLS = 3         # an empty target is judged only after this many crawls
FAIL_REPEAT = 3        # same error this many runs in a row
RUN_WINDOW_DAYS = 60


def days_cz(n: int) -> str:
    if n == 1:
        return "1 den"
    return f"{n} dny" if 2 <= n <= 4 else f"{n} dní"


def _label(t: CrawlTarget) -> str:
    return t.name or t.url


def _recent_runs(session: Session, now: datetime) -> dict[int, list[CrawlRun]]:
    """target id → its runs of the last RUN_WINDOW_DAYS, newest first."""
    rows = (session.query(CrawlRun)
            .filter(CrawlRun.target_id.isnot(None),
                    CrawlRun.finished_at >= now - timedelta(days=RUN_WINDOW_DAYS))
            .order_by(CrawlRun.target_id, CrawlRun.finished_at.desc()).all())
    out: dict[int, list[CrawlRun]] = defaultdict(list)
    for r in rows:
        out[r.target_id].append(r)
    return out


def _estimated_crawls(t: CrawlTarget, recorded: int) -> int:
    """Recorded runs, or — before crawl_runs existed — the crawls the
    target's age and interval imply."""
    if t.last_crawled_at is None:
        return recorded
    est = 1
    if t.created_at and t.interval_hours:
        est += int((t.last_crawled_at - t.created_at).total_seconds()
                   // (t.interval_hours * 3600))
    return max(recorded, est)


def _target_link(t: CrawlTarget, pids: list[int], stats) -> str:
    """The fed program with most episodes; the source URL when none is known."""
    if not pids:
        return t.url
    best = max(pids, key=lambda p: stats[p].episodes if p in stats else 0)
    return link("programs", best)


def detect_silent_targets(session: Session, now: datetime | None = None) -> list[Finding]:
    """(a) AUTO and book/drama targets with 0 episodes after MIN_CRAWLS
    crawls; AUTO targets with no new episode for STALL_DAYS."""
    now = now or utcnow()
    targets = [t for t in active_targets(session)
               if t.approval_mode == ApprovalMode.AUTO or is_book_name(t.name)]
    if not targets:
        return []
    mapping = target_programs(session, targets)
    stats = program_stats(session)
    runs = _recent_runs(session, now)
    counts = dict(session.query(CrawlRun.target_id, func.count(CrawlRun.id))
                  .filter(CrawlRun.target_id.isnot(None))
                  .group_by(CrawlRun.target_id).all())
    out = []
    for t in targets:
        auto = t.approval_mode == ApprovalMode.AUTO
        pids = mapping.get(t.id, [])
        n_eps = sum(stats[p].episodes for p in pids if p in stats)
        crawls = _estimated_crawls(t, counts.get(t.id, 0))
        target_link = _target_link(t, pids, stats)
        if n_eps == 0:
            if crawls < MIN_CRAWLS:
                continue
            why = "" if pids else " (nenalezen pořad — URL se nerozkládá na epizody?)"
            out.append(Finding("silent_targets", "warn" if auto else "info", f"target:{t.id}",
                               f"{_label(t)} [{t.approval_mode.name}]: 0 epizod po "
                               f"{crawls} crawlech{why}", target_link, "empty"))
            continue
        if not auto:
            continue
        newest = max((stats[p].newest for p in pids if p in stats and stats[p].newest),
                     default=None)
        if newest is None or now - newest < timedelta(days=STALL_DAYS):
            continue
        recent = runs.get(t.id, [])[:STALL_RUNS]
        if any(r.new_episodes for r in recent):
            continue
        age = (now - newest).days
        out.append(Finding("silent_targets", "warn", f"target:{t.id}",
                           f"{_label(t)} [AUTO]: žádná nová epizoda {days_cz(age)} "
                           f"(poslední {newest:%Y-%m-%d})", target_link, "stalled"))
    out.sort(key=lambda f: (f.severity != "warn", f.message))
    return out


def _error_streak(runs: list[CrawlRun]) -> tuple[int, str]:
    """(length, signature) of the newest-first run of identical errors."""
    if not runs or not runs[0].error:
        return 0, ""
    sig = error_signature(runs[0].error)
    streak = 0
    for r in runs:
        if not r.error or error_signature(r.error) != sig:
            break
        streak += 1
    return streak, sig


def detect_failing_targets(session: Session, now: datetime | None = None) -> list[Finding]:
    """(b) targets whose last FAIL_REPEAT runs failed with the same error."""
    now = now or utcnow()
    runs = _recent_runs(session, now)
    hits: dict[tuple[int, str], tuple[int, str]] = {}
    for tid, rs in runs.items():
        # one streak per path: the daily show sweep and the hourly crawl of
        # the same target interleave, a failing path must not hide behind
        # a healthy one
        by_path: dict[str, list[CrawlRun]] = defaultdict(list)
        for r in rs:
            by_path[r.path].append(r)
        for path, prs in by_path.items():
            streak = _error_streak(prs)
            if streak[0] >= FAIL_REPEAT:
                hits[(tid, path)] = streak
    if not hits:
        return []
    targets = {t.id: t for t in session.query(CrawlTarget)
               .filter(CrawlTarget.id.in_({tid for tid, _ in hits}))}
    out = []
    for (tid, path), (streak, sig) in sorted(hits.items(), key=lambda kv: -kv[1][0]):
        t = targets.get(tid)
        if t is None:
            continue
        auto = t.approval_mode == ApprovalMode.AUTO
        out.append(Finding("failing_targets", "warn" if auto else "info",
                           f"target:{tid}:{path}",
                           f"{_label(t)} [{t.approval_mode.name}, {path}]: {streak}× po sobě "
                           f"stejná chyba — {sig}", t.url, sig))
    return out
