"""Serial sweep — the safety net for audio literature.

The crawler ingests article pages as single-episode stubs; when the article
is actually a multi-part serial (Letní čtení / Četba style), the parts never
enter the index and the book silently expires (707 serials lost before this
existed, found 2026-09-06). This module resolves literature stubs through
api.mujrozhlas.cz serial-redirect and, for serials with live audio, creates
the missing episodes plus priority download jobs.

Politeness: ~1 request/second against rAPI, matching the spider budget.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import structlog
from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    AssetType, AvailabilityStatus, DownloadJob, Episode, JobStatus,
)

log = structlog.get_logger()

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_API = "https://api.mujrozhlas.cz"
_NODE_RE = re.compile(r"-(\d{6,8})/?$")
_SERIAL_RE = re.compile(r"serial/([0-9a-f-]{36})")

# Literature program name fragments (normalized/lowercase, diacritics kept
# and stripped variants both listed — matched via SQL LIKE on lower(name)).
LITERATURE_PATTERNS = (
    "%četb%", "%cetb%", "%čtení%", "%cteni%", "%povídk%", "%povidk%",
    "%počteníčko%", "%poctenicko%", "%osudy%", "%hra na%", "%rozhlasová hra%",
    "%rozhlasova hra%", "%audioknih%", "%radiokniha%", "%klasik%",
)
REQUEST_GAP_S = 1.05


def _http(url: str, follow: bool = True):
    req = urllib.request.Request(url, headers=_UA)
    if not follow:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            return opener.open(req, timeout=20)
        except urllib.error.HTTPError as e:
            return e
    return urllib.request.urlopen(req, timeout=20)


def _literature_stubs(session: Session, days: int) -> list[tuple[int, str, int, str]]:
    """(episode_id, url, work_id, work_title) for single-episode works
    without complete audio in literature programs, seen within *days*."""
    name_filter = " OR ".join(
        f"lower(p.name) LIKE '{pat}'" for pat in LITERATURE_PATTERNS)
    return session.execute(_sql_text(f"""
        SELECT e.id, e.url, w.id, w.title
        FROM episodes e
        JOIN works w ON e.work_id = w.id
        JOIN series se ON w.series_id = se.id
        JOIN programs p ON se.program_id = p.id
        WHERE e.url LIKE '%rozhlas.cz%'
          AND COALESCE(e.last_seen_at, e.first_seen_at, e.created_at)
              >= datetime('now', :window)
          AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.episode_id = e.id
                          AND a.type = 'AUDIO' AND a.status = 'COMPLETE')
          AND (SELECT COUNT(*) FROM episodes e2 WHERE e2.work_id = w.id) = 1
          AND ({name_filter})
    """), {"window": f"-{days} day"}).fetchall()


def _expand_serial(session: Session, serial: str, work_id: int) -> int:
    """Create episodes + priority jobs for the serial's live parts. Returns
    number of parts queued. Idempotent by ext_id."""
    data = json.loads(_http(f"{_API}/serials/{serial}/episodes").read())
    created = 0
    for e in data.get("data", []):
        a = e["attributes"]
        links = a.get("audioLinks") or []
        if not links:
            continue
        if session.query(Episode).filter_by(ext_id=e["id"]).first():
            continue
        hls = next((l for l in links if l.get("variant") == "hls"), links[0])
        ep = Episode(
            work_id=work_id, ext_id=e["id"],
            title=a.get("title") or f"{a.get('part')}. díl",
            episode_number=a.get("part"), url=hls["url"],
            duration_ms=(hls.get("duration") or 0) * 1000 or None,
            summary=a.get("description"),
            availability_status=AvailabilityStatus.AVAILABLE,
            auto_download=True, priority=90,
            discovery_source="serial-sweep",
        )
        session.add(ep)
        session.flush()
        session.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                                status=JobStatus.PENDING,
                                reason=f"serial-sweep: {serial[:8]}"))
        created += 1
    if created:
        session.commit()
    return created


def run_show_sweep(session: Session, max_targets: int | None = None) -> dict:
    """rAPI show-episode sweep for AUTO program targets.

    The mujrozhlas show pages are client-rendered, so the HTML crawler often
    sees nothing while the show carries live episodes (found live 2026-09-22:
    Pokračování za pět minut had 5 live readings, zero in DB). This sweep
    resolves each AUTO target's rozhlas node → show UUID → rAPI episode list
    and ingests unknown live episodes with download jobs.

    Works are grouped per episode title (a daily-reading slot airs several
    books side by side); the program is matched by crawl target name.
    """
    targets = session.execute(_sql_text("""
        SELECT ct.url, ct.name, p.id
        FROM crawl_targets ct
        JOIN programs p ON lower(p.name) = lower(ct.name)
        WHERE ct.approval_mode = 'AUTO' AND ct.active = 1
          AND ct.kind = 'PROGRAM' AND ct.url LIKE '%rozhlas.cz%'
    """)).fetchall()
    stats = {"targets": 0, "shows_resolved": 0, "new_episodes": 0, "err": 0}
    known = {r[0] for r in session.execute(_sql_text(
        "SELECT DISTINCT ext_id FROM episodes WHERE ext_id IS NOT NULL")).fetchall()}
    for url, tname, program_id in targets[:max_targets] if max_targets else targets:
        m = _NODE_RE.search(url or "")
        if not m:
            continue
        stats["targets"] += 1
        try:
            r = _http(f"{_API}/show-redirect/{m.group(1)}", follow=False)
            time.sleep(REQUEST_GAP_S)
            loc = r.headers.get("Location", "") if r.code in (301, 302) else ""
            um = re.search(r"show/([0-9a-f-]{36})", loc)
            if not um:
                continue
            stats["shows_resolved"] += 1
            d = json.loads(_http(
                f"{_API}/shows/{um.group(1)}/episodes?page%5Blimit%5D=100").read())
            time.sleep(REQUEST_GAP_S)
            series_id = session.execute(_sql_text(
                "SELECT id FROM series WHERE program_id = :p LIMIT 1"),
                {"p": program_id}).scalar()
            if series_id is None:
                continue
            for e in d.get("data", []):
                a = e["attributes"]
                links = a.get("audioLinks") or []
                if not links or e["id"] in known:
                    continue
                title = (a.get("title") or "").strip() or e["id"]
                work_id = session.execute(_sql_text(
                    "SELECT w.id FROM works w JOIN series se ON w.series_id = se.id "
                    "WHERE se.program_id = :p AND w.title = :t LIMIT 1"),
                    {"p": program_id, "t": title}).scalar()
                if work_id is None:
                    author = title.split(":", 1)[0].strip() if ":" in title[:60] else None
                    from audiobiblio.core.db.models import Work
                    w = Work(series_id=series_id, title=title, author=author)
                    session.add(w)
                    session.flush()
                    work_id = w.id
                hls = next((l for l in links if l.get("variant") == "hls"), links[0])
                ep = Episode(
                    work_id=work_id, ext_id=e["id"], title=title,
                    episode_number=a.get("part"), url=hls["url"],
                    duration_ms=(hls.get("duration") or 0) * 1000 or None,
                    summary=a.get("description"),
                    availability_status=AvailabilityStatus.AVAILABLE,
                    auto_download=True, priority=10,
                    discovery_source="show-sweep",
                )
                session.add(ep)
                session.flush()
                session.add(DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                                        status=JobStatus.PENDING,
                                        reason=f"show-sweep: {tname}"))
                known.add(e["id"])
                stats["new_episodes"] += 1
            session.commit()
        except Exception as ex:
            # One "database is locked" without rollback poisoned the session
            # and cascaded every remaining target into failure (2026-09-23:
            # 23 errors, 0 new episodes — Čas vos missed). Always roll back.
            session.rollback()
            stats["err"] += 1
            log.warning("show_sweep_error", url=url, error=str(ex))
    log.info("show_sweep_done", **stats)
    return stats


def run_serial_sweep(session: Session, days: int = 60,
                     max_stubs: int | None = None) -> dict:
    """One sweep pass over recent literature stubs. Returns stats."""
    stubs = _literature_stubs(session, days)
    if max_stubs:
        stubs = stubs[:max_stubs]
    stats = {"stubs": len(stubs), "serial_live": 0, "serial_expired": 0,
             "nonserial": 0, "queued_parts": 0, "err": 0}
    for ep_id, url, work_id, wtitle in stubs:
        m = _NODE_RE.search(url or "")
        if not m:
            continue
        try:
            r = _http(f"{_API}/serial-redirect/{m.group(1)}", follow=False)
            time.sleep(REQUEST_GAP_S)
            loc = r.headers.get("Location", "") if r.code in (301, 302) else ""
            sm = _SERIAL_RE.search(loc)
            if not sm:
                stats["nonserial"] += 1
                continue
            queued = _expand_serial(session, sm.group(1), work_id)
            time.sleep(REQUEST_GAP_S)
            if queued:
                stats["serial_live"] += 1
                stats["queued_parts"] += queued
                log.info("serial_sweep_queued", work_id=work_id,
                         title=wtitle[:60], parts=queued)
            else:
                stats["serial_expired"] += 1
        except Exception as ex:
            session.rollback()
            stats["err"] += 1
            log.warning("serial_sweep_error", url=url, error=str(ex))
    log.info("serial_sweep_done", **stats)
    return stats
