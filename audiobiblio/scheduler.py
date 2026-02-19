"""
scheduler — APScheduler-based periodic crawling and download execution.
"""
from __future__ import annotations
import signal
import sys
import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import load_config
from .crawler import run_due_crawls
from .downloader import run_pending_jobs
from .availability import check_unknown_episodes, process_watch_list

log = structlog.get_logger()


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
        n = run_pending_jobs(limit=cfg.download_batch_size)
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
