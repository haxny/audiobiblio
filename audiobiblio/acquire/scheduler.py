"""
scheduler — APScheduler-based periodic crawling and download execution.
"""
from __future__ import annotations
import signal
import sys
from datetime import datetime, timedelta
import structlog
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from audiobiblio.core.config import load_config
from audiobiblio.acquire.crawler import run_due_crawls
from audiobiblio.acquire.downloader import run_pending_jobs
from audiobiblio.acquire.availability import check_unknown_episodes, process_watch_list
from audiobiblio.library.trash import purge_trash

log = structlog.get_logger()

SYNC_TAGS_COMMIT_EVERY = 50
CRAWL_START_DELAY_MIN = 2


def _crawl_job():
    """Scheduled job: run all due crawl targets."""
    try:
        n = run_due_crawls()
        if n:
            log.info("crawl_cycle_done", jobs_queued=n)
    except Exception as e:
        log.error("crawl_cycle_error", error=str(e))


def _download_job():
    """Scheduled job: execute pending download jobs."""
    try:
        cfg = load_config()
        from audiobiblio.acquire.downloader import _is_night
        # night window (19-05) is the heavy-lifting time — big batches;
        # daytime stays small (the 30/h cap inside the runner rules anyway)
        limit = 120 if _is_night() else cfg.download_batch_size
        n = run_pending_jobs(limit=limit)
        if n:
            log.info("download_cycle_done", jobs_executed=n)
    except Exception as e:
        log.error("download_cycle_error", error=str(e))


def _availability_job():
    """Scheduled job: check availability for unknown episodes and watch list."""
    try:
        checked = check_unknown_episodes(limit=50)
        requeued = process_watch_list()
        if checked or requeued:
            log.info("availability_cycle_done", checked=checked, requeued=requeued)
    except Exception as e:
        log.error("availability_cycle_error", error=str(e))


def _purge_trash_job():
    """Scheduled job: purge expired trash (>retention_days old)."""
    try:
        cfg = load_config()
        library_dir = Path(cfg.library_dir).expanduser()
        count = purge_trash(library_dir, cfg.trash_retention_days)
        if count:
            log.info("purge_trash_cycle_done", folders_removed=count)
    except Exception as e:
        log.error("purge_trash_cycle_error", error=str(e))


def _sync_tags_job():
    """Nightly DB→file tag projection. The curated-shelf guard inside
    sync_episode_tags keeps hand-made files untouched (only MANUAL values
    rewrite there); working-library files follow the DB fully."""
    try:
        from audiobiblio.core.db.session import get_session
        from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode
        from audiobiblio.library.sync import sync_episode_tags
        s = get_session()
        ep_ids = [eid for (eid,) in s.query(Asset.episode_id).filter(
            Asset.type == AssetType.AUDIO, Asset.status == AssetStatus.COMPLETE,
            Asset.file_path.isnot(None)).all()]
        rewrote = 0
        for n, eid in enumerate(ep_ids, 1):
            ep = s.get(Episode, eid)
            if ep is None:
                continue
            try:
                rep = sync_episode_tags(s, ep, write=True)
                rewrote += sum(1 for d in rep.diffs if d.action == "rewrite")
            except Exception:
                s.rollback()
                log.warning("sync_tags_episode_failed", episode_id=eid, exc_info=True)
            if n % SYNC_TAGS_COMMIT_EVERY == 0:
                # Short write transactions: one commit at the end held the
                # SQLite write lock for ~3.5 h every night and the crawler
                # died on 'database is locked' (2026-10-09).
                s.commit()
        s.commit()
        log.info("sync_tags_done", episodes=len(ep_ids), fields_rewritten=rewrote)
    except Exception as e:
        log.error("sync_tags_cycle_error", error=str(e))


def _serial_sweep_job():
    """Scheduled job: expand literature serial-stubs into live parts and
    ingest live show episodes the HTML crawler cannot see."""
    try:
        from audiobiblio.core.db.session import get_session
        from audiobiblio.acquire.serial_sweep import run_serial_sweep, run_show_sweep
        stats = run_serial_sweep(get_session())
        log.info("serial_sweep_job", **stats)
        show_stats = run_show_sweep(get_session())
        log.info("show_sweep_job", **show_stats)
    except Exception as e:
        log.error("serial_sweep_job_error", error=str(e))


def _auto_finalize_job():
    """Scheduled job: the librarian — finished books move to curated shelves."""
    try:
        from audiobiblio.core.db.session import get_session
        from audiobiblio.library.pipelines.auto_finalize import run_auto_finalize
        report = run_auto_finalize(get_session())
        for line in report:
            log.info("auto_finalize", line=line)
    except Exception as e:
        log.error("auto_finalize_cycle_error", error=str(e))


def _panacek_sync_job():
    """Monthly: fetch mluvenypanacek.cz changes since the last sync (user rule:
    at least once a month). Parsing/enrichment works on the stored dump."""
    try:
        from datetime import datetime, timezone
        from audiobiblio.paths import get_dirs
        from audiobiblio.sources.mluvenypanacek import sync
        st = sync(get_dirs()["data"] / "panacek",
                  now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
        log.info("panacek_sync_job", **st)
    except Exception as e:
        log.error("panacek_sync_job_error", error=str(e))


def _abs_rescan_job():
    """Rescan ABS libraries we shelved into today (ABS's watcher misses our
    new folders; a normal scan picks up new books + metadata.json)."""
    try:
        from audiobiblio.core.json_state import load_json, save_json
        from audiobiblio.library.abs import AbsClient
        from audiobiblio.library.abs_metadata import DIRTY_FILE
        dirty = [name for name, flag in load_json(DIRTY_FILE).items() if flag]
        if not dirty:
            return
        client = AbsClient.from_config()
        libs = {l["name"]: l["id"] for l in client.get_libraries()}
        for name in dirty:
            if name in libs:
                client.trigger_scan(libs[name])
                log.info("abs_rescan_triggered", library=name)
        save_json(DIRTY_FILE, {})
    except Exception as e:
        log.error("abs_rescan_error", error=str(e))


OWNED_ROOTS = ("/media/fiction", "/media/nonfiction", "/media/ebooks/4kids", "/media/audiobooks")


def _fix_ownership_job():
    """Safety net: whatever the root container left root-owned goes back to
    the folder's owner, so the user can edit tags over SMB (2026-10-09)."""
    from pathlib import Path
    from audiobiblio.core.fsperm import fix_root_owned
    for root in OWNED_ROOTS:
        try:
            n = fix_root_owned(Path(root))
            if n:
                log.info("ownership_fixed", root=root, entries=n)
        except Exception as e:
            log.error("ownership_fix_error", root=root, error=str(e))


def create_scheduler(
    crawl_interval_minutes: int = 60,
    download_interval_minutes: int = 5,
) -> BackgroundScheduler:
    """
    Create a BackgroundScheduler with crawl, download, and availability jobs.
    Does NOT start it — caller is responsible for .start() and .shutdown().
    """
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        _crawl_job,
        trigger=IntervalTrigger(minutes=crawl_interval_minutes),
        id="crawl_due_targets",
        name="Crawl due targets",
        replace_existing=True,
        max_instances=1,
        # First cycle soon after start: every deploy/restart used to idle the
        # crawl for a full interval (2026-10-09: 1,133 targets left waiting
        # after a day of restarts). Progress lives in next_crawl_at.
        next_run_time=datetime.now() + timedelta(minutes=CRAWL_START_DELAY_MIN),
    )

    scheduler.add_job(
        _download_job,
        trigger=IntervalTrigger(minutes=download_interval_minutes),
        id="run_pending_downloads",
        name="Run pending downloads",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _availability_job,
        trigger=IntervalTrigger(hours=6),
        id="check_availability",
        name="Check episode availability",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _purge_trash_job,
        trigger=IntervalTrigger(hours=24),
        id="purge_trash",
        name="Purge expired trash",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _auto_finalize_job,
        # Fixed clock time, not interval: a 24h interval resets on every
        # container restart, so with frequent crashes/deploys the job could
        # go weeks without a single run (observed 07-26 → 09-06).
        trigger=CronTrigger(hour=3, minute=7),
        id="auto_finalize",
        name="Shelve finished books (auto-finalize)",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _panacek_sync_job,
        trigger=CronTrigger(day=1, hour=2, minute=15),
        id="panacek_sync",
        name="mluvenypanacek.cz monthly sync",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _fix_ownership_job,
        trigger=CronTrigger(hour=3, minute=35),
        id="fix_ownership",
        name="Hand root-owned library files back to their folder owner",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _abs_rescan_job,
        # after the librarian (03:07) has shelved the night's books
        trigger=CronTrigger(hour=3, minute=45),
        id="abs_rescan",
        name="Rescan ABS libraries shelved into",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _serial_sweep_job,
        # Nightly literature safety net: expands serial article-stubs via
        # rAPI so multi-part books can never silently expire again (707
        # serials were lost to this gap before 2026-09-06).
        trigger=CronTrigger(hour=5, minute=41),
        id="serial_sweep",
        name="Serial sweep (literature stubs → live parts)",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        _sync_tags_job,
        trigger=CronTrigger(hour=4, minute=23),
        id="sync_tags",
        name="Project DB metadata into file tags (nightly)",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler


def start_scheduler(
    crawl_interval_minutes: int = 60,
    download_interval_minutes: int = 5,
):
    """
    Start the blocking scheduler with crawl and download jobs.
    Backward-compat CLI entrypoint for `audiobiblio scheduler`.
    """
    sched = BlockingScheduler()

    for job in create_scheduler(crawl_interval_minutes, download_interval_minutes).get_jobs():
        sched.add_job(
            job.func,
            trigger=job.trigger,
            id=job.id,
            name=job.name,
            replace_existing=True,
            max_instances=1,
        )

    # Run once immediately on startup
    _crawl_job()
    _download_job()

    def _shutdown(signum, frame):
        log.info("scheduler_shutdown", signal=signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("scheduler_started",
             crawl_interval=f"{crawl_interval_minutes}m",
             download_interval=f"{download_interval_minutes}m")
    sched.start()
