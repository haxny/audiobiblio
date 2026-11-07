"""A download never overwrites a file another episode owns as COMPLETE.

Live case 2026-11-07: duplicate rows of the same part (rAPI + gap-fill) had
the same target path; the gap-fill download overwrote the clean rAPI copy.
"""
from __future__ import annotations

from pathlib import Path

from audiobiblio.acquire import downloader
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, DownloadJob, JobStatus,
)


def _setup(db_session, episode_factory, tmp_path, monkeypatch):
    owner = episode_factory()
    dup = episode_factory()
    target = tmp_path / "Povidka (CRo3)" / "Part 03.m4a"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"clean rapi copy")
    db_session.add(Asset(episode_id=owner.id, type=AssetType.AUDIO,
                         status=AssetStatus.COMPLETE, file_path=str(target.resolve())))
    job = DownloadJob(episode_id=dup.id, asset_type=AssetType.AUDIO,
                      status=JobStatus.PENDING, reason="asset:AUDIO; gap-fill")
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(downloader, "build_paths_for_episode",
                        lambda ep, work=None: {"base_dir": target.parent, "stem": "Part 03"})
    calls = []

    def fake_ytdlp(*a, **k):
        calls.append(a)
        target.write_bytes(b"ad variant")
        return target
    monkeypatch.setattr(downloader, "_run_ytdlp_audio", fake_ytdlp)
    monkeypatch.setattr(downloader, "get_session", lambda: db_session)
    monkeypatch.setattr(downloader, "_day_quota_exhausted", lambda *a: False)
    return owner, dup, job, target, calls


def test_download_skips_when_target_owned_by_other_episode(
        db_session, episode_factory, tmp_path, monkeypatch):
    owner, dup, job, target, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch)
    downloader.run_pending_jobs()
    assert calls == []
    assert target.read_bytes() == b"clean rapi copy"
    assert job.status == JobStatus.SKIPPED
    assert str(owner.id) in (job.error or "")
    asset = db_session.query(Asset).filter_by(episode_id=dup.id, type=AssetType.AUDIO).one()
    assert asset.status == AssetStatus.SKIPPED


def test_download_proceeds_when_target_is_own_or_free(
        db_session, episode_factory, tmp_path, monkeypatch):
    owner, dup, job, target, calls = _setup(db_session, episode_factory, tmp_path, monkeypatch)
    # re-point the owner's asset elsewhere → path is free for dup
    a = db_session.query(Asset).filter_by(episode_id=owner.id).one()
    a.file_path = str(Path(target.parent / "Other.m4a").resolve())
    db_session.commit()
    monkeypatch.setattr(downloader, "tag_audio", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "apply_media_info", lambda *a, **k: None)
    downloader.run_pending_jobs()
    assert len(calls) == 1
    assert job.status == JobStatus.SUCCESS
