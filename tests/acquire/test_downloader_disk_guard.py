"""Downloads wait for free space instead of filling the disk.

Live 2026-11: volume3 at ~100 % while repeats and new serials kept queuing.
Hard floor for every download, a much higher one for optional repeats; a held
job stays PENDING (nothing is lost) and an unreadable disk never blocks.
"""
from __future__ import annotations

from audiobiblio.acquire import downloader
from audiobiblio.core.db.models import DownloadJob, JobStatus, AssetType


def _setup(db_session, episode_factory, tmp_path, monkeypatch, *, reason, free_gb):
    ep = episode_factory()
    job = DownloadJob(episode_id=ep.id, asset_type=AssetType.AUDIO,
                      status=JobStatus.PENDING, reason=reason)
    db_session.add(job)
    db_session.commit()
    target = tmp_path / "lib" / "Part 01.m4a"
    monkeypatch.setattr(downloader, "build_paths_for_episode",
                        lambda ep, work=None: {"base_dir": target.parent, "stem": "Part 01"})
    calls = []

    def fake_ytdlp(*a, **k):
        calls.append(a)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"audio")
        return target
    monkeypatch.setattr(downloader, "_run_ytdlp_audio", fake_ytdlp)
    monkeypatch.setattr(downloader, "get_session", lambda: db_session)
    monkeypatch.setattr(downloader, "_day_quota_exhausted", lambda *a: False)
    monkeypatch.setattr(downloader, "tag_audio", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "apply_media_info", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "_free_gb", lambda path: free_gb)
    monkeypatch.delenv("AUDIOBIBLIO_MIN_FREE_GB", raising=False)
    monkeypatch.delenv("AUDIOBIBLIO_REPEAT_MIN_FREE_GB", raising=False)
    return job, calls


def test_df_alone_never_stops_everyday_downloads(db_session, episode_factory, tmp_path, monkeypatch):
    """df on Synology btrfs is pessimistic (3 GB by df, hundreds of GiB real):
    no df floor by default."""
    job, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                        reason="asset:AUDIO; show-sweep: X", free_gb=3)
    assert downloader.run_pending_jobs() == 1
    assert job.status == JobStatus.SUCCESS


def test_pause_flag_holds_audio_downloads(db_session, episode_factory, tmp_path, monkeypatch):
    job, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                        reason="show-sweep: X", free_gb=500)
    flag = tmp_path / "PAUSE_DOWNLOADS"
    flag.write_text("")
    monkeypatch.setenv("AUDIOBIBLIO_PAUSE_FILE", str(flag))
    assert downloader.run_pending_jobs() == 0
    assert calls == [] and job.status == JobStatus.PENDING
    flag.unlink()
    assert downloader.run_pending_jobs() == 1       # resumes, nothing was lost
    assert job.status == JobStatus.SUCCESS


def test_normal_download_runs_with_50_gb_but_a_repeat_waits(
        db_session, episode_factory, tmp_path, monkeypatch):
    """The live state: ~49 GB free. Everyday downloads keep working, optional
    repeats wait for more headroom."""
    normal, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                           reason="show-sweep: X", free_gb=50)
    assert downloader.run_pending_jobs() == 1
    assert len(calls) == 1 and normal.status == JobStatus.SUCCESS

    rep, calls2 = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                         reason="repeat: show-sweep: X", free_gb=50)
    assert downloader.run_pending_jobs() == 0
    assert calls2 == [] and rep.status == JobStatus.PENDING


def test_repeat_downloads_when_there_is_headroom(db_session, episode_factory, tmp_path, monkeypatch):
    job, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                        reason="repeat: serial-sweep: abc", free_gb=150)
    assert downloader.run_pending_jobs() == 1
    assert job.status == JobStatus.SUCCESS


def test_unreadable_disk_never_blocks(db_session, episode_factory, tmp_path, monkeypatch):
    job, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                        reason="repeat: show-sweep: X", free_gb=None)
    assert downloader.run_pending_jobs() == 1
    assert job.status == JobStatus.SUCCESS


def test_twenty_gb_by_df_does_not_stop_everyday_downloads(
        db_session, episode_factory, tmp_path, monkeypatch):
    """Synology btrfs: df says ~20 GB while ~700 GiB are unallocated — the
    hard floor must not turn that number into an outage."""
    job, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch,
                        reason="show-sweep: X", free_gb=20)
    assert downloader.run_pending_jobs() == 1
