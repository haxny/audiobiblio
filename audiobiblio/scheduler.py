"""
scheduler — APScheduler-based periodic crawling and download execution.
"""
from __future__ import annotations
import signal
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

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
        n = run_pending_jobs(limit=10)
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


def start_scheduler(
    crawl_interval_minutes: int = 60,
    download_interval_minutes: int = 5,
):
    """
    Start the blocking scheduler with crawl and download jobs.
    This is the Docker entrypoint for long-running operation.
    """
    scheduler = BlockingScheduler()

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

    # Run once immediately on startup
    _crawl_job()
    _download_job()

    def _shutdown(signum, frame):
        log.info("scheduler_shutdown", signal=signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Start health check endpoint in background
    _start_health_server()

    log.info("scheduler_started",
             crawl_interval=f"{crawl_interval_minutes}m",
             download_interval=f"{download_interval_minutes}m")
    scheduler.start()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logging


def _start_health_server(port: int = 8080):
    """Start a minimal HTTP health check server in a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info("health_server_started", port=port)
