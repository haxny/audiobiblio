"""Library-side detectors: expiring episodes, the librarian's queue, the
retry cap, disk space, episodes overwriting each other's file."""
from __future__ import annotations

import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import func
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, Episode, JobStatus, Series, Work,
)
from audiobiblio.core.time import utcnow
from audiobiblio.library.health.detectors_crawl import days_cz
from audiobiblio.library.health.findings import Finding, error_signature, link
from audiobiblio.library.health.scope import book_program_ids

EXPIRY_DAYS = 14
EXPIRY_CRIT_DAYS = 3
LIBRARIAN_WARN_DAYS = 30
RETRY_WINDOW_HOURS = 24
DISK_WARN_FREE_PCT = 10.0
DISK_CRIT_FREE_PCT = 5.0
IN_CHUNK = 500


def _chunks(seq, n=IN_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _complete_audio(session: Session, episode_ids) -> set[int]:
    out: set[int] = set()
    for chunk in _chunks(episode_ids):
        out.update(eid for (eid,) in session.query(Asset.episode_id).filter(
            Asset.episode_id.in_(chunk), Asset.type == AssetType.AUDIO,
            Asset.status == AssetStatus.COMPLETE))
    return out


def _last_job_status(session: Session, episode_ids) -> dict[int, str]:
    out: dict[int, str] = {}
    for chunk in _chunks(episode_ids):
        for eid, status in (session.query(DownloadJob.episode_id, DownloadJob.status)
                            .filter(DownloadJob.episode_id.in_(chunk),
                                    DownloadJob.asset_type == AssetType.AUDIO)
                            .order_by(DownloadJob.id)):
            out[eid] = status.name
    return out


def detect_expiring(session: Session, now: datetime | None = None) -> list[Finding]:
    """(c) not-downloaded book/drama episodes whose live audio expires
    within EXPIRY_DAYS (rAPI playableTill)."""
    now = now or utcnow()
    rows = (session.query(Episode.id, Episode.title, Episode.playable_till, Work.id,
                          Work.title, Work.expected_total, Series.program_id)
            .join(Work, Episode.work_id == Work.id)
            .join(Series, Work.series_id == Series.id)
            .filter(Episode.playable_till > now,
                    Episode.playable_till <= now + timedelta(days=EXPIRY_DAYS))
            .order_by(Episode.playable_till).all())
    if not rows:
        return []
    books = book_program_ids(session)
    rows = [r for r in rows if r[6] in books or (r[5] or 0) > 1]
    done = _complete_audio(session, [r[0] for r in rows])
    rows = [r for r in rows if r[0] not in done]
    jobs = _last_job_status(session, [r[0] for r in rows])
    out = []
    for eid, ep_title, till, wid, w_title, _total, _pid in rows:
        left = till - now
        days = int(left.total_seconds() // 86400)
        sev = "crit" if left <= timedelta(days=EXPIRY_CRIT_DAYS) else "warn"
        name = w_title if ep_title == w_title else f"{w_title} — {ep_title}"
        out.append(Finding("expiring", sev, f"episode:{eid}",
                           f"{name}: vyprší za {days_cz(days)} ({till:%Y-%m-%d}), "
                           f"nestaženo (job: {jobs.get(eid, 'žádný')})",
                           link("works", wid), jobs.get(eid, "NO-JOB")))
    return out


def _librarian_verdicts(session: Session, now: datetime) -> list[dict]:
    from audiobiblio.library.pipelines.auto_finalize import run_auto_finalize
    verdicts: list[dict] = []
    run_auto_finalize(session, dry_run=True, now=now, verdicts=verdicts)
    session.rollback()  # dry run: make sure nothing it touched lingers
    return verdicts


def detect_librarian_stuck(session: Session, now: datetime | None = None,
                           verdicts: list[dict] | None = None) -> list[Finding]:
    """(d) finished works the librarian does not shelve, oldest wait first.
    Age = days since the newest audio of the work was downloaded."""
    now = now or utcnow()
    if verdicts is None:
        verdicts = _librarian_verdicts(session, now)
    stuck = [v for v in verdicts if v["kind"] != "SHELVE"]
    if not stuck:
        return []
    ids = [v["work_id"] for v in stuck]
    ready: dict[int, datetime] = {}
    titles: dict[int, str] = {}
    for chunk in _chunks(ids):
        ready.update(session.query(Episode.work_id, func.max(Asset.first_seen_at))
                     .join(Asset, Asset.episode_id == Episode.id)
                     .filter(Episode.work_id.in_(chunk), Asset.type == AssetType.AUDIO,
                             Asset.status == AssetStatus.COMPLETE)
                     .group_by(Episode.work_id).all())
        titles.update(session.query(Work.id, Work.title).filter(Work.id.in_(chunk)).all())
    aged = sorted(((now - ready[v["work_id"]]).days if ready.get(v["work_id"]) else 0, i, v)
                  for i, v in enumerate(stuck))
    out = []
    for age, _, v in reversed(aged):  # oldest wait first
        wid = v["work_id"]
        sev = "warn" if age > LIBRARIAN_WARN_DAYS else "info"
        detail = (v.get("detail") or "")[:120]
        out.append(Finding("librarian", sev, f"work:{wid}",
                           f"{titles.get(wid, wid)}: {v['kind']} — {detail}; čeká {days_cz(age)}",
                           link("works", wid), v["kind"]))
    return out


def detect_retry_cap_errors(session: Session, now: datetime | None = None) -> list[Finding]:
    """(e) assets that reached the failed-attempt cap with an error in the
    last RETRY_WINDOW_HOURS — they will not be re-planned automatically."""
    from audiobiblio.library.pipelines.checks import MAX_FAILED_ATTEMPTS
    now = now or utcnow()
    cutoff = now - timedelta(hours=RETRY_WINDOW_HOURS)
    capped = (session.query(DownloadJob.episode_id, DownloadJob.asset_type,
                            func.count(DownloadJob.id))
              .filter(DownloadJob.status == JobStatus.ERROR)
              .group_by(DownloadJob.episode_id, DownloadJob.asset_type)
              .having(func.count(DownloadJob.id) >= MAX_FAILED_ATTEMPTS)
              .having(func.max(DownloadJob.finished_at) >= cutoff).all())
    if not capped:
        return []
    want = {(eid, at) for eid, at, _ in capped}
    last_error: dict[tuple, str] = {}
    titles: dict[int, str] = {}
    for chunk in _chunks({eid for eid, _ in want}):
        for eid, at, err in (session.query(DownloadJob.episode_id, DownloadJob.asset_type,
                                           DownloadJob.error)
                             .filter(DownloadJob.episode_id.in_(chunk),
                                     DownloadJob.status == JobStatus.ERROR)
                             .order_by(DownloadJob.finished_at, DownloadJob.id)):
            last_error[(eid, at)] = err or ""
        titles.update(session.query(Episode.id, Episode.title).filter(Episode.id.in_(chunk)))
    out = []
    for eid, at, n in capped:
        sig = error_signature(last_error.get((eid, at))) or "?"
        out.append(Finding("retry_cap", "warn", f"job:{eid}:{at.name}",
                           f"{titles.get(eid, eid)} [{at.name}]: {n}× chyba — {sig}",
                           link("episodes", eid), sig))
    out.sort(key=lambda f: (f.group, f.key))
    return out


def disk_paths() -> list[str]:
    """Media roots to watch: env AUDIOBIBLIO_HEALTH_DISK_PATHS (comma list),
    else the configured library_dir."""
    env = os.environ.get("AUDIOBIBLIO_HEALTH_DISK_PATHS", "")
    if env.strip():
        return [p.strip() for p in env.split(",") if p.strip()]
    from audiobiblio.core.config import load_config
    return [os.path.expanduser(load_config().library_dir)]


def detect_disk_space(paths: list[str] | None = None, usage_fn=shutil.disk_usage) -> list[Finding]:
    """(f) free space on the media roots below the warn/crit thresholds."""
    out = []
    for path in paths if paths is not None else disk_paths():
        try:
            u = usage_fn(path)
        except OSError as e:
            out.append(Finding("disk", "warn", f"disk:{path}:error",
                               f"{path}: nelze zjistit volné místo ({e})"))
            continue
        free_pct = 100.0 * u.free / u.total if u.total else 0.0
        if free_pct >= DISK_WARN_FREE_PCT:
            continue
        sev = "crit" if free_pct < DISK_CRIT_FREE_PCT else "warn"
        out.append(Finding("disk", sev, f"disk:{path}:{sev}",
                           f"{path}: volno jen {free_pct:.0f} % "
                           f"({u.free / 1e9:.0f} GB z {u.total / 1e9:.0f} GB)"))
    return out


def _episodes_cz(n: int) -> str:
    return f"{n} epizody" if 2 <= n <= 4 else f"{n} epizod"


def detect_shared_files(session: Session, now: datetime | None = None) -> list[Finding]:
    """(g) complete audio files claimed by more than one episode — weekly
    shows numbered '01' overwrote each other (Oldies, 2026-11-07)."""
    shared = (session.query(Asset.file_path, func.count(Asset.id))
              .filter(Asset.type == AssetType.AUDIO, Asset.status == AssetStatus.COMPLETE,
                      Asset.file_path.isnot(None))
              .group_by(Asset.file_path).having(func.count(Asset.id) > 1).all())
    if not shared:
        return []
    work_of: dict[str, Counter] = defaultdict(Counter)
    for chunk in _chunks([p for p, _ in shared]):
        for path, wid in (session.query(Asset.file_path, Episode.work_id)
                          .join(Episode, Asset.episode_id == Episode.id)
                          .filter(Asset.file_path.in_(chunk), Asset.type == AssetType.AUDIO,
                                  Asset.status == AssetStatus.COMPLETE)):
            work_of[path][wid] += 1
    out = []
    for path, n in sorted(shared, key=lambda r: (-r[1], r[0])):
        wid = work_of[path].most_common(1)[0][0] if work_of[path] else None
        out.append(Finding("shared_files", "warn", f"path:{path}",
                           f"{_episodes_cz(n)} sdílí soubor {PurePosixPath(path).name}",
                           link("works", wid) if wid else None))
    return out
