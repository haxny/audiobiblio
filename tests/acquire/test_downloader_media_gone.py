"""Gone media goes to WATCH, not ERROR; orphaned RUNNING jobs are reset.

Live case 2026-11-08: 86 "Básnění" (Vltava) jobs failed overnight. The page
still lists the parts, but the mp3 files return 404 — the content is gone.
The job error only said "returned non-zero exit status 1" because yt-dlp's
stderr was dropped. Separately, 4 jobs sat in RUNNING for a day: the
container restart during a deploy killed their download mid-flight.
"""
from __future__ import annotations

import subprocess
from datetime import timedelta

import requests

from audiobiblio.acquire import availability, downloader
from audiobiblio.core.db.models import (
    AvailabilityStatus, DownloadJob, JobStatus,
)
from audiobiblio.core.time import utcnow

YTDLP_404 = ("[info] 8355007: Downloading 1 format(s): mp3\n"
             "ERROR: unable to download video data: HTTP Error 404: Not Found\n")


def _job(db_session, episode_factory, status=JobStatus.PENDING):
    ep = episode_factory()
    job = DownloadJob(episode_id=ep.id, asset_type=downloader.AssetType.AUDIO, status=status)
    db_session.add(job)
    db_session.commit()
    return ep, job


def _wire(monkeypatch, db_session, tmp_path, fake):
    monkeypatch.setattr(downloader, "build_paths_for_episode",
                        lambda ep, work=None: {"base_dir": tmp_path, "stem": "x"})
    monkeypatch.setattr(downloader, "_run_ytdlp_audio", fake)
    monkeypatch.setattr(downloader, "get_session", lambda: db_session)
    monkeypatch.setattr(downloader, "_day_quota_exhausted", lambda *a: False)


def test_ytdlp_failure_keeps_stderr_and_flags_gone_media(monkeypatch, tmp_path):
    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr=YTDLP_404)
    monkeypatch.setattr(downloader.subprocess, "run", boom)
    monkeypatch.setattr(downloader, "_yt_dlp_cmd", lambda: ["yt-dlp"])
    try:
        downloader._run_ytdlp_audio("https://vltava.rozhlas.cz/x-1", tmp_path, "x", ext_id="1")
    except downloader.MediaGone as e:
        assert "HTTP Error 404" in str(e)
    else:
        raise AssertionError("expected MediaGone")


def test_other_ytdlp_failure_is_plain_error_with_stderr(monkeypatch, tmp_path):
    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="ERROR: Unsupported URL\n")
    monkeypatch.setattr(downloader.subprocess, "run", boom)
    monkeypatch.setattr(downloader, "_yt_dlp_cmd", lambda: ["yt-dlp"])
    try:
        downloader._run_ytdlp_audio("https://x/y", tmp_path, "x")
    except downloader.MediaGone:
        raise AssertionError("not a gone-media case")
    except RuntimeError as e:
        assert "Unsupported URL" in str(e)


def test_gone_media_job_goes_to_watch(db_session, episode_factory, tmp_path, monkeypatch):
    ep, job = _job(db_session, episode_factory)

    def fake(*a, **k):
        raise downloader.MediaGone("HTTP Error 404: Not Found")
    _wire(monkeypatch, db_session, tmp_path, fake)
    downloader.run_pending_jobs()
    assert job.status == JobStatus.WATCH
    assert job.finished_at is not None
    assert downloader.MEDIA_GONE_MARK in job.error


def test_http_404_from_requests_is_gone_media():
    r = requests.Response()
    r.status_code = 404
    assert downloader.is_media_gone(requests.HTTPError("404 Client Error", response=r))
    r2 = requests.Response()
    r2.status_code = 503
    assert not downloader.is_media_gone(requests.HTTPError("503", response=r2))
    assert not downloader.is_media_gone(RuntimeError("disk full"))


def test_reset_orphaned_running_jobs(db_session, episode_factory, monkeypatch):
    _, stuck = _job(db_session, episode_factory, JobStatus.RUNNING)
    stuck.started_at = utcnow()
    _, done = _job(db_session, episode_factory, JobStatus.SUCCESS)
    db_session.commit()
    monkeypatch.setattr(downloader, "get_session", lambda: db_session)
    assert downloader.reset_orphaned_running() == 1
    assert stuck.status == JobStatus.PENDING and stuck.started_at is None
    assert done.status == JobStatus.SUCCESS


def _watch_job(db_session, episode_factory, days_ago):
    ep, job = _job(db_session, episode_factory, JobStatus.WATCH)
    job.error = f"\n{downloader.MEDIA_GONE_MARK} HTTP Error 404"
    job.finished_at = utcnow() - timedelta(days=days_ago)
    db_session.commit()
    return job


def test_watch_does_not_requeue_gone_media_just_because_page_is_up(
        db_session, episode_factory, monkeypatch):
    """The page stays up while the file is gone — re-queueing on every
    availability tick would loop download → 404 → WATCH forever."""
    fresh = _watch_job(db_session, episode_factory, days_ago=2)
    old = _watch_job(db_session, episode_factory, days_ago=40)
    monkeypatch.setattr(availability, "get_session", lambda: db_session)
    monkeypatch.setattr(availability, "check_episode_availability",
                        lambda s, ep: AvailabilityStatus.AVAILABLE)
    assert availability.process_watch_list() == 1
    assert fresh.status == JobStatus.WATCH
    assert old.status == JobStatus.PENDING
