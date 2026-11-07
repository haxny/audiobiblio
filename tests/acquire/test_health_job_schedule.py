"""The daily health check is scheduled after the night jobs and never
lets an exception escape into APScheduler."""
from __future__ import annotations

from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger

from audiobiblio.acquire import scheduler


def test_health_job_is_scheduled_daily_after_the_sweeps():
    sched = scheduler.create_scheduler()
    job = sched.get_job("health_check")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("6", "30")


def test_health_job_swallows_errors():
    with patch("audiobiblio.library.health.job.run_health_check",
               side_effect=RuntimeError("boom")), \
         patch("audiobiblio.core.db.session.get_session"):
        scheduler._health_job()  # must not raise


def test_health_job_runs_the_check():
    with patch("audiobiblio.library.health.job.run_health_check",
               return_value={"worse": 0}) as run, \
         patch("audiobiblio.core.db.session.get_session"):
        scheduler._health_job()
    run.assert_called_once()
