"""Live recorder — ffmpeg captures planned windows; restart-safe by design.

Production redeploys rebuild the container, killing anything in flight.
So nothing lives only in memory:

* ffmpeg is bounded by `-t <seconds left in the window>` — even if the
  app hangs, the process ends on time by itself;
* it writes ADTS/MP3 *segments* (`r<run>_<n>.aac`, 5 min each) which are
  valid up to the last frame when the process is killed — no moov atom
  to lose;
* a tick (every 30 s, and right after start-up) reconciles the DB with
  reality: starts due windows, resumes a RECORDING row whose process is
  gone with a new run for the rest of the window (adopting an orphan
  ffmpeg that is still alive), finalizes ended windows (also ones that
  ended while the app was down), and marks windows missed entirely as
  FAILED — promoting the rerun BACKUP airing.

Finalize = join all segments of all runs into one .m4a (stream copy),
ffprobe the duration, coverage = duration / window length →
COMPLETE (≥ 95 %), PARTIAL (kept, backup promoted) or FAILED.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import structlog
from sqlalchemy.orm import Session

from audiobiblio.acquire.live.planner import promote_backup
from audiobiblio.core.db.models import LiveRecording, LiveStatus
from audiobiblio.core.time import utcnow

log = structlog.get_logger()

SEGMENT_S = 300
COMPLETE_COVERAGE = 0.95
MIN_USABLE_S = 60
STOP_GRACE_S = 90           # ffmpeg ends itself via -t; kill only after this
RESTART_MIN_GAP_S = 15      # don't hammer a dead stream
FINALIZE_TIMEOUT_S = 1800


class Handle(Protocol):
    pid: int
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...


class Runner(Protocol):
    def start(self, cmd: list[str], log_path: Path) -> Handle: ...


class SubprocessRunner:
    def start(self, cmd: list[str], log_path: Path) -> Handle:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as fh:
            # own session: a uvicorn reload's SIGINT must not hit ffmpeg
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                                    start_new_session=True)


def build_ffmpeg_cmd(url: str, seconds: float, work_dir: Path, run_no: int) -> list[str]:
    ext = "mp3" if url.lower().endswith(".mp3") else "aac"
    seg_format = "mp3" if ext == "mp3" else "adts"
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
        "-reconnect_delay_max", "30", "-rw_timeout", "30000000",
        "-user_agent", "audiobiblio-live-recorder/1.0",
        "-i", url, "-t", str(int(math.ceil(seconds))), "-map", "0:a", "-c", "copy",
        "-f", "segment", "-segment_time", str(SEGMENT_S), "-segment_format", seg_format,
        "-reset_timestamps", "1",
        str(Path(work_dir) / f"r{run_no:02d}_%04d.{ext}"),
    ]


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def remux_segments(segments: list[Path], out: Path) -> Path:
    """Join segments (in order) into one file by stream copy. AAC → .m4a
    (ADTS→ASC bitstream filter, faststart); MP3 → .mp3."""
    out.parent.mkdir(parents=True, exist_ok=True)
    is_mp3 = segments[0].suffix.lower() == ".mp3"
    out = out.with_suffix(".mp3" if is_mp3 else ".m4a")
    lst = out.parent / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy"]
    if not is_mp3:
        cmd += ["-bsf:a", "aac_adtstoasc", "-movflags", "+faststart"]
    subprocess.run(cmd + [str(out)], check=True, capture_output=True, timeout=FINALIZE_TIMEOUT_S)
    lst.unlink(missing_ok=True)
    return out


def pid_alive(pid: int, marker: str) -> bool:
    """True if *pid* is a live ffmpeg writing into *marker* (our work dir)."""
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False        # no /proc (macOS dev box): never adopt blindly
    return "ffmpeg" in cmdline and marker in cmdline


def _catalog_default(session: Session, rec: LiveRecording, path: Path):
    from audiobiblio.acquire.live.catalog import catalog_recording
    return catalog_recording(session, rec, path)


def _work_root_default() -> Path:
    from audiobiblio.core.config import load_config
    cfg = load_config()
    if cfg.live_work_dir:
        return Path(cfg.live_work_dir).expanduser()
    return Path(cfg.download_dir).expanduser() / "_live"


class LiveRecorder:
    def __init__(self, work_root: Path | None = None, runner: Runner | None = None,
                 remux: Callable[[list[Path], Path], Path] = remux_segments,
                 probe: Callable[[Path], float] = ffprobe_duration,
                 catalog_fn: Callable = _catalog_default,
                 pid_alive: Callable[[int, str], bool] = pid_alive):
        self.work_root = Path(work_root) if work_root else _work_root_default()
        self.runner = runner or SubprocessRunner()
        self.remux, self.probe, self.catalog_fn, self.pid_alive = remux, probe, catalog_fn, pid_alive
        self._procs: dict[int, Handle] = {}

    # ── the tick ────────────────────────────────────────────────────
    def tick(self, session: Session, now: datetime | None = None) -> dict:
        now = now or utcnow()
        stats = {"started": 0, "resumed": 0, "finalized": 0, "missed": 0}
        rows = session.query(LiveRecording).filter(LiveRecording.status.in_((
            LiveStatus.PLANNED, LiveStatus.RECORDING, LiveStatus.FINALIZING)),
            LiveRecording.window_start <= now).order_by(LiveRecording.window_start).all()
        for rec in rows:
            try:
                self._step(session, rec, now, stats)
            except Exception as e:      # one bad row must not stop the others
                session.rollback()
                log.error("live_tick_error", id=rec.id, title=rec.title, error=str(e))
                rec.status, rec.error = LiveStatus.FAILED, f"tick error: {e}"[:2000]
            session.commit()
        return stats

    def _step(self, session: Session, rec: LiveRecording, now: datetime, stats: dict) -> None:
        if rec.status == LiveStatus.FINALIZING:
            self._finalize(session, rec, now)
            stats["finalized"] += 1
        elif rec.status == LiveStatus.PLANNED:
            if now >= rec.window_end:
                self._fail(session, rec, now, "missed: the app was not running during the window")
                stats["missed"] += 1
            else:
                self._start_run(rec, now)
                stats["started"] += 1
        elif rec.status == LiveStatus.RECORDING:
            if now >= rec.window_end:
                if self._stop_due(rec, now):
                    self._finalize(session, rec, now)
                    stats["finalized"] += 1
            elif not self._is_running(rec) and self._may_restart(rec, now):
                self._start_run(rec, now)
                stats["resumed"] += 1

    # ── runs ────────────────────────────────────────────────────────
    def _work_dir(self, rec: LiveRecording) -> Path:
        d = Path(rec.work_dir) if rec.work_dir else self.work_root / str(rec.id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _start_run(self, rec: LiveRecording, now: datetime) -> None:
        work = self._work_dir(rec)
        runs = list(rec.runs or [])
        run_no = len(runs) + 1
        seconds = (rec.window_end - now).total_seconds()
        cmd = build_ffmpeg_cmd(rec.stream_url, seconds, work, run_no)
        handle = self.runner.start(cmd, work / "ffmpeg.log")
        self._procs[rec.id] = handle
        runs.append({"run": run_no, "pid": handle.pid, "started_at": now.isoformat(),
                     "seconds": int(math.ceil(seconds))})
        rec.runs, rec.work_dir, rec.status = runs, str(work), LiveStatus.RECORDING
        log.info("live_record_run_started", id=rec.id, title=rec.title, run=run_no,
                 pid=handle.pid, seconds=int(seconds), stream=rec.stream_url,
                 resumed=run_no > 1)

    def _is_running(self, rec: LiveRecording) -> bool:
        h = self._procs.get(rec.id)
        if h is not None:
            return h.poll() is None
        last = (rec.runs or [{}])[-1]
        if last.get("pid") and self.pid_alive(last["pid"], rec.work_dir or ""):
            log.info("live_record_orphan_adopted", id=rec.id, pid=last["pid"])
            return True
        return False

    def _may_restart(self, rec: LiveRecording, now: datetime) -> bool:
        last = (rec.runs or [{}])[-1]
        started = last.get("started_at")
        if started and (now - datetime.fromisoformat(started)).total_seconds() < RESTART_MIN_GAP_S:
            return False
        h = self._procs.get(rec.id)
        if h is not None:
            log.warning("live_record_run_ended_early", id=rec.id, returncode=h.poll(),
                        run=last.get("run"))
        return True

    def _stop_due(self, rec: LiveRecording, now: datetime) -> bool:
        """True when no process remains; kills a straggler after the grace."""
        h = self._procs.get(rec.id)
        alive = h.poll() is None if h is not None else self._is_running(rec)
        if not alive:
            return True
        if (now - rec.window_end).total_seconds() < STOP_GRACE_S:
            return False
        if h is not None:
            h.terminate()
            try:
                h.wait(timeout=10)
            except Exception:
                pass
        else:
            pid = (rec.runs or [{}])[-1].get("pid")
            try:
                os.kill(pid, 15)
            except (OSError, TypeError):
                pass
        log.warning("live_record_terminated_after_grace", id=rec.id)
        return True

    # ── finalize ────────────────────────────────────────────────────
    def _finalize(self, session: Session, rec: LiveRecording, now: datetime) -> None:
        self._procs.pop(rec.id, None)
        rec.status = LiveStatus.FINALIZING
        session.commit()
        work = Path(rec.work_dir) if rec.work_dir else self.work_root / str(rec.id)
        segments = sorted(p for p in work.glob("r*_*.*")
                          if p.suffix in (".aac", ".mp3") and p.stat().st_size > 0) \
            if work.is_dir() else []
        if not segments:
            self._fail(session, rec, now, "no audio captured (stream unreachable?)")
            return
        (work / "final").mkdir(parents=True, exist_ok=True)
        try:
            out = self.remux(segments, work / "final" / f"live-{rec.id}.m4a")
        except Exception as e:
            self._fail(session, rec, now, f"remux failed: {e}")
            return
        rec.file_path = str(out)
        duration = float(self.probe(out) or 0)
        planned = (rec.window_end - rec.window_start).total_seconds()
        rec.duration_s, rec.coverage = duration, round(min(duration / planned, 1.0), 4) if planned else 0
        if duration < MIN_USABLE_S:
            self._fail(session, rec, now, f"only {duration:.0f}s captured")
            return
        try:
            self.catalog_fn(session, rec, out)
        except Exception as e:
            session.rollback()
            rec.file_path, rec.duration_s = str(out), duration
            self._fail(session, rec, now, f"catalog failed: {e}")
            return
        complete = rec.coverage >= COMPLETE_COVERAGE
        rec.status = LiveStatus.COMPLETE if complete else LiveStatus.PARTIAL
        rec.error = None if complete else (
            f"gaps: {duration / 60:.0f} of {planned / 60:.0f} min ({len(rec.runs or [])} runs)")
        if complete:
            self._skip_backups(session, rec)
            self._cleanup(work, keep=Path(rec.file_path) if rec.file_path else None)
        else:
            promote_backup(session, rec.dedupe_key, now)
        log.info("live_record_finalized", id=rec.id, title=rec.title, status=rec.status.value,
                 coverage=rec.coverage, duration_min=round(duration / 60, 1),
                 runs=len(rec.runs or []), file=rec.file_path)

    def _fail(self, session: Session, rec: LiveRecording, now: datetime, why: str) -> None:
        rec.status, rec.error = LiveStatus.FAILED, why[:2000]
        log.error("live_record_failed", id=rec.id, title=rec.title, reason=why)
        promote_backup(session, rec.dedupe_key, now)

    @staticmethod
    def _skip_backups(session: Session, rec: LiveRecording) -> None:
        for b in session.query(LiveRecording).filter(
                LiveRecording.dedupe_key == rec.dedupe_key,
                LiveRecording.status.in_((LiveStatus.BACKUP, LiveStatus.PLANNED)),
                LiveRecording.id != rec.id).all():
            b.status, b.error = LiveStatus.SKIPPED, f"recorded by #{rec.id}"

    @staticmethod
    def _cleanup(work: Path, keep: Path | None) -> None:
        """Segments go once the joined file sits in the library."""
        if keep is not None and work in keep.parents:
            return              # catalog did not move it — keep everything
        shutil.rmtree(work, ignore_errors=True)


_DEFAULT: LiveRecorder | None = None


def default_recorder() -> LiveRecorder:
    """Process-wide recorder: its process registry must outlive one tick."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LiveRecorder()
    return _DEFAULT
