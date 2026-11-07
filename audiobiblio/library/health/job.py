"""Daily health check: run every detector, store one snapshot per detector
per day (so a rising count is visible), send ONE digest only when
something is new or worse than the baseline.

Baseline = the previous day's snapshot; after a digest went out today,
today's snapshot (so a same-day rerun reports only what is newer still).
"Worse" = a new warn/crit finding, a higher worst severity, or a
detector that started crashing. Info-level findings show on /system only.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import CrawlRun, HealthSnapshot
from audiobiblio.core.notify import Notifier, health_notifier
from audiobiblio.core.time import utcnow
from audiobiblio.library.health.findings import SEVERITY_RANK, public_url, worst

log = structlog.get_logger()

TOP_ITEMS = 30
DIGEST_ITEMS = 5
CRAWL_RUN_RETENTION_DAYS = 90
TREND_DAYS = 7
WARN = SEVERITY_RANK["warn"]


def _rank(sev: str | None) -> int:
    return SEVERITY_RANK.get(sev or "", -1)


def _baseline(session: Session, detector: str, day: str,
              today: HealthSnapshot | None) -> HealthSnapshot | None:
    if today is not None and today.notified:
        return today
    return (session.query(HealthSnapshot)
            .filter(HealthSnapshot.detector == detector, HealthSnapshot.day < day)
            .order_by(HealthSnapshot.day.desc()).first())


def _new_serious(findings, base: HealthSnapshot | None) -> list:
    old = {k for k, s in ((base.keys or {}) if base else {}).items() if _rank(s) >= WARN}
    return [f for f in findings if _rank(f.severity) >= WARN and f.key not in old]


def _store(session: Session, snap: HealthSnapshot | None, day: str, det_id: str,
           findings, error: str | None) -> HealthSnapshot:
    ordered = sorted(findings, key=lambda f: -_rank(f.severity))  # stable: keeps detector order
    if snap is None:
        snap = HealthSnapshot(day=day, detector=det_id, notified=False)
        session.add(snap)
    snap.count = len(findings)
    snap.severity = worst(findings)
    snap.keys = {f.key: f.severity for f in findings}
    snap.details = {"items": [f.to_dict() for f in ordered[:TOP_ITEMS]],
                    "groups": dict(Counter(f.group for f in findings if f.group))}
    snap.error = error[:1000] if error else None
    snap.created_at = utcnow()
    return snap


def _digest_section(det, findings, base, error, new) -> str:
    if error:
        return f"■ {det.label}: detektor selhal — {error[:200]}"
    prev = f"včera {base.count}" if base is not None else "nové"
    lines = [f"■ {det.label}: {len(findings)} ({prev})"]
    for f in new[:DIGEST_ITEMS]:
        lines.append(f"  - {f.message}")
        if f.link:
            lines.append(f"    {f.link}")
    if len(new) > DIGEST_ITEMS:
        lines.append(f"  … a dalších {len(new) - DIGEST_ITEMS} nových")
    return "\n".join(lines)


def _prune_crawl_runs(session: Session, now: datetime) -> int:
    n = (session.query(CrawlRun)
         .filter(CrawlRun.finished_at < now - timedelta(days=CRAWL_RUN_RETENTION_DAYS))
         .delete(synchronize_session=False))
    session.commit()
    return n


def run_health_check(session: Session, now: datetime | None = None,
                     notifier: Notifier | None = None, detectors=None) -> dict:
    if detectors is None:
        from audiobiblio.library.health.detectors import DETECTORS
        detectors = DETECTORS
    now = now or utcnow()
    day = now.strftime("%Y-%m-%d")
    sections, counts, snaps = [], {}, []
    for det in detectors:
        error = None
        try:
            findings = det.fn(session, now)
        except Exception as e:
            session.rollback()
            findings, error = [], f"{type(e).__name__}: {e}"
            log.error("health_detector_failed", detector=det.id, error=error)
        today = (session.query(HealthSnapshot)
                 .filter_by(day=day, detector=det.id).one_or_none())
        base = _baseline(session, det.id, day, today)
        new = _new_serious(findings, base)
        rose = _rank(worst(findings)) > max(_rank(base.severity) if base else -1, 0)
        crashed = error is not None and not (base is not None and base.error)
        if new or crashed or (rose and _rank(worst(findings)) >= WARN):
            sections.append(_digest_section(det, findings, base, error, new))
        snaps.append(_store(session, today, day, det.id, findings, error))
        session.commit()
        counts[det.id] = len(findings)
    pruned = _prune_crawl_runs(session, now)
    notified = False
    if sections:
        notifier = notifier or health_notifier()
        body = "\n\n".join(sections) + f"\n\nPřehled: {public_url()}/system"
        notified = notifier.send(f"audiobiblio — zdraví {day}: {len(sections)}× horší", body)
        if notified:
            for s in snaps:
                s.notified = True
            session.commit()
    log.info("health_check_done", day=day, counts=counts, worse=len(sections),
             notified=notified, crawl_runs_pruned=pruned)
    return {"day": day, "counts": counts, "worse": len(sections), "notified": notified}


def health_overview(session: Session) -> list[dict]:
    """/system rows: latest snapshot per detector, trend vs TREND_DAYS earlier."""
    from audiobiblio.library.health.detectors import DETECTORS
    latest_day = (session.query(HealthSnapshot.day)
                  .order_by(HealthSnapshot.day.desc()).limit(1).scalar())
    by_det: dict[str, list[HealthSnapshot]] = {}
    if latest_day:
        since = (datetime.strptime(latest_day, "%Y-%m-%d")
                 - timedelta(days=TREND_DAYS * 2)).strftime("%Y-%m-%d")
        for s in (session.query(HealthSnapshot).filter(HealthSnapshot.day >= since)
                  .order_by(HealthSnapshot.day.desc())):
            by_det.setdefault(s.detector, []).append(s)
    rows = []
    for det in DETECTORS:
        snaps = by_det.get(det.id, [])
        cur = snaps[0] if snaps else None
        trend = None
        if cur is not None:
            ref_day = (datetime.strptime(cur.day, "%Y-%m-%d")
                       - timedelta(days=TREND_DAYS)).strftime("%Y-%m-%d")
            ref = next((s for s in snaps if s.day <= ref_day), None)
            trend = cur.count - ref.count if ref is not None else None
        details = (cur.details or {}) if cur else {}
        rows.append({
            "id": det.id, "label": det.label,
            "count": cur.count if cur else None, "trend": trend,
            "day": cur.day if cur else None, "severity": cur.severity if cur else None,
            "error": cur.error if cur else None,
            "top": (details.get("items") or [])[:10],
            "groups": details.get("groups") or {},
        })
    return rows
