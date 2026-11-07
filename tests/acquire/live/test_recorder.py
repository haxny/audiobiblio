"""Recorder lifecycle: start at window start, survive restarts, finalize.

ffmpeg is replaced by a fake runner (handles with pid/poll/terminate);
the finalize step gets a fake remux/probe except in the one integration
test that runs real ffmpeg on two synthetic ADTS segments.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from audiobiblio.acquire.live.recorder import (
    COMPLETE_COVERAGE, LiveRecorder, build_ffmpeg_cmd, ffprobe_duration, remux_segments,
)
from audiobiblio.core.db.models import LiveRecording, LiveStatus

T0 = datetime(2026, 10, 10, 5, 59)          # window start (naive UTC)
T1 = T0 + timedelta(hours=4, minutes=3)      # window end


class FakeHandle:
    _next_pid = 1000

    def __init__(self, cmd):
        FakeHandle._next_pid += 1
        self.pid = FakeHandle._next_pid
        self.cmd = cmd
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class FakeRunner:
    def __init__(self):
        self.started: list[FakeHandle] = []

    def start(self, cmd, log_path):
        h = FakeHandle(cmd)
        self.started.append(h)
        # what ffmpeg would leave behind: one segment per run
        out_pattern = Path(cmd[-1])
        out_pattern.parent.mkdir(parents=True, exist_ok=True)
        (out_pattern.parent / out_pattern.name.replace("%04d", "0000")).write_bytes(b"seg")
        return h


def _rec(db, **kw) -> LiveRecording:
    defaults = dict(
        station_id="e26a14b0-35d5-3b55-a6c8-679d2c6e0526", station_code="CRoDDur", kind="block",
        dedupe_key="blok i", title="Blok I.", series_title="Blok", part=1,
        schedule_start=T0 + timedelta(seconds=60), schedule_end=T1 - timedelta(seconds=120),
        window_start=T0, window_end=T1, stream_url="https://rozhlas.stream/ddur_high.aac",
        items=[{"since": "2026-10-10T08:00:00+02:00", "till": "2026-10-10T12:00:00+02:00",
                "headline": "Blok I.", "title": "Anton Bruckner: Symfonie č. 7", "description": "d"}],
        description="popis", status=LiveStatus.PLANNED, runs=[])
    defaults.update(kw)
    r = LiveRecording(**defaults)
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def runner():
    return FakeRunner()


def _recorder(tmp_path, runner, **kw):
    kw.setdefault("pid_alive", lambda pid, marker: False)
    kw.setdefault("catalog_fn", lambda session, rec, path: None)
    return LiveRecorder(work_root=tmp_path / "live", runner=runner, **kw)


def test_build_ffmpeg_cmd_is_bounded_resumable_and_segmented(tmp_path):
    cmd = build_ffmpeg_cmd("https://rozhlas.stream/ddur_high.aac", 3600.4, tmp_path, run_no=2)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-t") + 1] == "3601"
    assert "-reconnect" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[cmd.index("-f") + 1] == "segment"
    assert cmd[-1].endswith("r02_%04d.aac")
    assert build_ffmpeg_cmd("https://rozhlas.stream/ddur.mp3", 10, tmp_path, 1)[-1].endswith("r01_%04d.mp3")


def test_nothing_happens_before_window(db_session, tmp_path, runner):
    _rec(db_session)
    _recorder(tmp_path, runner).tick(db_session, now=T0 - timedelta(minutes=1))
    assert runner.started == []


def test_starts_at_window_start_with_remaining_duration(db_session, tmp_path, runner):
    rec = _rec(db_session)
    _recorder(tmp_path, runner).tick(db_session, now=T0 + timedelta(seconds=20))
    assert len(runner.started) == 1
    cmd = runner.started[0].cmd
    assert int(cmd[cmd.index("-t") + 1]) == int((T1 - T0).total_seconds()) - 20
    db_session.refresh(rec)
    assert rec.status == LiveStatus.RECORDING
    assert rec.runs[0]["pid"] == runner.started[0].pid
    assert rec.work_dir and Path(rec.work_dir).is_dir()


def test_running_process_is_left_alone(db_session, tmp_path, runner):
    _rec(db_session)
    r = _recorder(tmp_path, runner)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    r.tick(db_session, now=T0 + timedelta(minutes=30))
    assert len(runner.started) == 1


def test_restart_resumes_with_new_run_for_the_rest_of_the_window(db_session, tmp_path, runner):
    rec = _rec(db_session)
    _recorder(tmp_path, runner).tick(db_session, now=T0 + timedelta(seconds=5))
    # container restart: fresh recorder, empty process registry, old pid dead
    later = T0 + timedelta(hours=1)
    _recorder(tmp_path, runner).tick(db_session, now=later)
    assert len(runner.started) == 2
    cmd = runner.started[1].cmd
    assert cmd[-1].endswith("r02_%04d.aac")
    assert int(cmd[cmd.index("-t") + 1]) == int((T1 - later).total_seconds())
    db_session.refresh(rec)
    assert [r["run"] for r in rec.runs] == [1, 2]


def test_restart_adopts_orphan_ffmpeg_still_alive(db_session, tmp_path, runner):
    _rec(db_session)
    _recorder(tmp_path, runner).tick(db_session, now=T0 + timedelta(seconds=5))
    _recorder(tmp_path, runner, pid_alive=lambda pid, marker: True).tick(
        db_session, now=T0 + timedelta(hours=1))
    assert len(runner.started) == 1


def test_premature_exit_restarts_stream(db_session, tmp_path, runner):
    _rec(db_session)
    r = _recorder(tmp_path, runner)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    runner.started[0].returncode = 1          # stream dropped
    r.tick(db_session, now=T0 + timedelta(minutes=10))
    assert len(runner.started) == 2


def test_missed_window_fails_and_promotes_backup(db_session, tmp_path, runner):
    rec = _rec(db_session)
    backup = _rec(db_session, status=LiveStatus.BACKUP,
                  window_start=T0 + timedelta(hours=18), window_end=T1 + timedelta(hours=18),
                  schedule_start=T0 + timedelta(hours=18, seconds=60))
    _recorder(tmp_path, runner).tick(db_session, now=T1 + timedelta(hours=1))
    db_session.refresh(rec); db_session.refresh(backup)
    assert rec.status == LiveStatus.FAILED and "missed" in rec.error
    assert backup.status == LiveStatus.PLANNED
    assert runner.started == []


def _finalizing_recorder(tmp_path, runner, duration, catalog_calls, source_file=None):
    def remux(segments, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        if source_file:
            shutil.copy(source_file, out)
        else:
            out.write_bytes(b"".join(p.read_bytes() for p in segments))
        return out

    return _recorder(tmp_path, runner, remux=remux, probe=lambda p: duration,
                     catalog_fn=lambda s, rec, path: catalog_calls.append((rec.id, path)))


def test_finalize_after_window_end_completes_and_skips_backups(db_session, tmp_path, runner):
    rec = _rec(db_session)
    backup = _rec(db_session, status=LiveStatus.BACKUP,
                  window_start=T0 + timedelta(hours=18), window_end=T1 + timedelta(hours=18),
                  schedule_start=T0 + timedelta(hours=18, seconds=60))
    calls: list = []
    r = _finalizing_recorder(tmp_path, runner, duration=(T1 - T0).total_seconds() - 30, catalog_calls=calls)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    runner.started[0].returncode = 0
    r.tick(db_session, now=T1 + timedelta(seconds=40))
    db_session.refresh(rec); db_session.refresh(backup)
    assert rec.status == LiveStatus.COMPLETE
    assert rec.coverage >= COMPLETE_COVERAGE
    assert rec.duration_s == pytest.approx((T1 - T0).total_seconds() - 30)
    assert calls and calls[0][0] == rec.id
    assert backup.status == LiveStatus.SKIPPED


def test_finalize_waits_for_ffmpeg_to_exit_then_kills_after_grace(db_session, tmp_path, runner):
    rec = _rec(db_session)
    calls: list = []
    r = _finalizing_recorder(tmp_path, runner, duration=(T1 - T0).total_seconds(), catalog_calls=calls)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    r.tick(db_session, now=T1 + timedelta(seconds=10))        # still running, inside grace
    assert not runner.started[0].terminated and not calls
    r.tick(db_session, now=T1 + timedelta(minutes=3))         # past grace → terminate + finalize
    assert runner.started[0].terminated
    db_session.refresh(rec)
    assert rec.status == LiveStatus.COMPLETE


def test_partial_recording_is_kept_and_backup_promoted(db_session, tmp_path, runner):
    rec = _rec(db_session)
    backup = _rec(db_session, status=LiveStatus.BACKUP,
                  window_start=T0 + timedelta(hours=18), window_end=T1 + timedelta(hours=18),
                  schedule_start=T0 + timedelta(hours=18, seconds=60))
    calls: list = []
    r = _finalizing_recorder(tmp_path, runner, duration=3600, catalog_calls=calls)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    runner.started[0].returncode = 0
    r.tick(db_session, now=T1 + timedelta(minutes=3))
    db_session.refresh(rec); db_session.refresh(backup)
    assert rec.status == LiveStatus.PARTIAL
    assert rec.coverage == pytest.approx(3600 / (T1 - T0).total_seconds(), abs=1e-4)
    assert calls  # a partial recording is still better than nothing
    assert backup.status == LiveStatus.PLANNED


def test_no_segments_means_failed(db_session, tmp_path):
    class NoOutputRunner(FakeRunner):
        def start(self, cmd, log_path):
            h = FakeHandle(cmd)
            self.started.append(h)
            return h
    runner = NoOutputRunner()
    rec = _rec(db_session)
    calls: list = []
    r = _finalizing_recorder(tmp_path, runner, duration=0, catalog_calls=calls)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    runner.started[0].returncode = 1
    r.tick(db_session, now=T1 + timedelta(minutes=3))
    db_session.refresh(rec)
    assert rec.status == LiveStatus.FAILED and not calls


def test_crash_during_finalize_is_retried(db_session, tmp_path, runner):
    rec = _rec(db_session, status=LiveStatus.FINALIZING)
    work = tmp_path / "live" / str(rec.id)
    work.mkdir(parents=True)
    (work / "r01_0000.aac").write_bytes(b"seg")
    rec.work_dir = str(work)
    db_session.commit()
    calls: list = []
    _finalizing_recorder(tmp_path, runner, duration=(T1 - T0).total_seconds(),
                         catalog_calls=calls).tick(db_session, now=T1 + timedelta(hours=2))
    db_session.refresh(rec)
    assert rec.status == LiveStatus.COMPLETE and calls


def test_catalog_error_marks_failed_but_keeps_file(db_session, tmp_path, runner):
    rec = _rec(db_session)

    def bad_catalog(s, r, p):
        raise RuntimeError("disk full")
    r = _recorder(tmp_path, runner, remux=lambda segs, out: (out.write_bytes(b"x"), out)[1],
                  probe=lambda p: (T1 - T0).total_seconds(), catalog_fn=bad_catalog)
    r.tick(db_session, now=T0 + timedelta(seconds=5))
    runner.started[0].returncode = 0
    r.tick(db_session, now=T1 + timedelta(minutes=3))
    db_session.refresh(rec)
    assert rec.status == LiveStatus.FAILED and "disk full" in rec.error
    assert rec.file_path and Path(rec.file_path).exists()


ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@ffmpeg
def test_real_remux_of_adts_segments_from_two_runs(tmp_path):
    segs = []
    for name, secs in (("r01_0000.aac", 1.0), ("r01_0001.aac", 1.0), ("r02_0000.aac", 0.5)):
        p = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=44100", "-t", str(secs),
                        "-c:a", "aac", "-f", "adts", str(p)], check=True)
        segs.append(p)
    out = remux_segments(segs, tmp_path / "out" / "final.m4a")
    assert out.exists() and out.suffix == ".m4a"
    assert ffprobe_duration(out) == pytest.approx(2.5, abs=0.2)
