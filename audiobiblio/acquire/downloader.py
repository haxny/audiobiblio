from __future__ import annotations
import shutil, subprocess, sys, time
from pathlib import Path

from audiobiblio.core.time import utcnow
import structlog
import requests
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from audiobiblio.core.db.models import DownloadJob, JobStatus, AssetType, AssetStatus, Episode, Asset, Work, Series, Program, Station
from audiobiblio.core.db.session import get_session
from audiobiblio.library.pipelines.library import build_paths_for_episode
from audiobiblio.library.pipelines.postprocess import tag_audio
# TODO(phase2→): decouple acquire->library via event bus / callback protocol
from audiobiblio.library.mediainfo import apply_media_info
from audiobiblio.library.enrich_meta import enrich_episode_from_meta

log = structlog.get_logger()

# ── Human-like download pacing (user rule 2026-07-24) ──────────────────────
# Bursts of 5 jobs, then 10 s of quiet. Heavy lifting belongs to the NIGHT
# window (19:00–05:00 Prague); during the day at most 30 audio files/hour.
from collections import deque
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

_PRAGUE = ZoneInfo("Europe/Prague")
BURST_SIZE = 5
BURST_PAUSE_S = 10
DAY_HOURLY_AUDIO_CAP = 30
NIGHT_START, NIGHT_END = 19, 5   # 19:00–05:00 = night (unrestricted volume)
_audio_done_at: deque = deque(maxlen=500)


def _is_night(now=None) -> bool:
    h = (now or _dt.now(_PRAGUE)).hour
    return h >= NIGHT_START or h < NIGHT_END


def _day_quota_exhausted(now_ts: float | None = None) -> bool:
    """True when the daytime hourly audio budget is spent."""
    if _is_night():
        return False
    now_ts = now_ts or time.time()
    hour_ago = now_ts - 3600
    recent = sum(1 for t in _audio_done_at if t >= hour_ago)
    return recent >= DAY_HOURLY_AUDIO_CAP

def _which(cmd: str) -> str | None:
    return shutil.which(cmd)

def _yt_dlp_cmd() -> list[str] | None:
    exe = _which("yt-dlp") or _which("yt_dlp")
    if exe:
        return [exe]
    # fallback to python -m if module is available
    try:
        import yt_dlp  # noqa
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        return None

def _safe_mkdir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _update_job(session, job: DownloadJob, status: JobStatus, error: str | None = None):
    job.status = status
    now = time.time()
    if status == JobStatus.RUNNING:
        job.started_at = job.started_at or utcnow()
    if status in (JobStatus.SUCCESS, JobStatus.ERROR, JobStatus.SKIPPED):
        job.finished_at = utcnow()
    if error:
        job.error = (job.error or "") + f"\n{error}"
    session.commit()

def _mark_asset_status(session, episode_id: int, t: AssetType, status: AssetStatus, **fields):
    a = session.scalar(select(Asset).where(Asset.episode_id == episode_id, Asset.type == t))
    if not a:
        a = Asset(episode_id=episode_id, type=t)
        session.add(a)
    a.status = status
    for k, v in fields.items():
        setattr(a, k, v)
    session.commit()


def _run_ytdlp_audio(url: str, out_dir: Path, stem: str, episode_number: int | None = None,
                     ext_id: str | None = None) -> Path:
    """Invoke yt-dlp to download audio from *url* into *out_dir*/{stem}.m4a.

    Entry selection: ext_id (yt-dlp entry id) is the ONLY stable identity —
    playlist POSITION shifts as parts expire and pages embed unrelated
    "related" players (found live: position-based download fetched a
    different BOOK). --match-filter "id=…" wins over --playlist-items.

    Returns the resolved path of the downloaded file.  Raises RuntimeError
    if yt-dlp is unavailable or the output file cannot be located after a
    successful run.  Callers are responsible for creating *out_dir* first.
    """
    cmd = _yt_dlp_cmd()
    if not cmd:
        raise RuntimeError("yt-dlp is not installed or importable")

    output_template = str(out_dir / f"{stem}.%(ext)s")

    # Build base command — tags written by shared tags package after download
    base_args = [
        "--extract-audio",
        "--audio-format", "m4a",
        "--audio-quality", "0",
        "--embed-thumbnail",
        "--no-download-archive",
        "--output", output_template,
    ]

    if ext_id:
        base_args.insert(0, "--match-filter")
        base_args.insert(1, f"id={ext_id}")
    elif episode_number is not None:
        base_args.insert(0, "--playlist-items")
        base_args.insert(1, str(episode_number))

    cmd.extend(base_args)
    cmd.append(url)

    log.info("yt-dlp_command", command=" ".join(cmd))
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    # Locate the actual output file (extension may differ from template)
    expected = Path(output_template.replace("%(ext)s", "m4a"))
    if expected.exists():
        return expected

    candidates = list(expected.parent.glob(f"{expected.stem}.*"))
    audio_exts = {".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac"}
    candidates = [c for c in candidates if c.suffix.lower() in audio_exts]
    if candidates:
        return candidates[0]

    raise RuntimeError(f"Download succeeded but output file not found: {expected}")


def download_to_staging(url: str, staging_dir: Path) -> Path:
    """Download audio from *url* into *staging_dir* without any DB writes.

    Returns the path of the downloaded file.  The staging dir is created if
    needed.  Uses the shared yt-dlp invocation (extract-audio m4a, quality 0,
    embed-thumbnail).  No episode_number / playlist-items filtering is applied
    — upgrade candidates always point to a single-episode URL.
    """
    _safe_mkdir(staging_dir)
    return _run_ytdlp_audio(url, staging_dir, "candidate")


def _download_audio(session, job: DownloadJob, ep: Episode, work: Work):
    if not ep.url:
        raise RuntimeError("Episode has no URL to download")

    log.info("download_audio", url=ep.url, episode=ep.id)

    paths = build_paths_for_episode(ep, work)

    # Make sure the final directory exists
    _safe_mkdir(paths["base_dir"])

    asset_path = _run_ytdlp_audio(ep.url, paths["base_dir"], paths["stem"],
                                  ep.episode_number, ext_id=ep.ext_id)

    # Write metadata tags via shared tags package (genre taxonomy, all formats)
    tag_audio(asset_path, ep, work)

    _mark_asset_status(session, ep.id, AssetType.AUDIO, AssetStatus.COMPLETE,
                       file_path=str(asset_path.resolve()), size_bytes=asset_path.stat().st_size)

    # Populate Asset quality fields (bitrate, channels, sample_rate, codec, container)
    # and backfill episode.duration_ms if still NULL.
    audio_asset = session.scalar(
        select(Asset).where(Asset.episode_id == ep.id, Asset.type == AssetType.AUDIO)
    )
    if audio_asset is not None:
        try:
            apply_media_info(session, audio_asset, asset_path)
        except Exception:
            log.warning("mediainfo_apply_failed", asset_id=audio_asset.id, exc_info=True)

def _download_meta_json(session, job: DownloadJob, episode: Episode, work: Work):
    if not episode.url:
        raise RuntimeError("Episode has no URL to fetch metadata")

    ytc = _yt_dlp_cmd()
    if not ytc:
        raise RuntimeError("yt-dlp is not installed or importable")

    paths = build_paths_for_episode(episode, work)
    out_dir = paths["base_dir"]
    _safe_mkdir(out_dir)

    out_tpl = str(out_dir / f"{paths['stem']}.%(ext)s")

    cmd = ytc + [
        "--no-playlist",
        "--write-info-json",
        "--skip-download",
        "-o", out_tpl,
        "--no-download-archive",
        episode.url,
    ]
    log.info("yt-dlp_command", command=" ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "yt-dlp failed")

    # Find .info.json produced
    json_candidates = list(out_dir.glob(f"{paths['stem']}*.info.json"))
    if not json_candidates:
        # yt-dlp may choose a different base; fallback to any .info.json updated recently
        json_candidates = sorted(out_dir.glob("*.info.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_candidates:
        raise RuntimeError("Info JSON not found after yt-dlp run")
    jf = json_candidates[0]
    _mark_asset_status(session, episode.id, AssetType.META_JSON, AssetStatus.COMPLETE,
                       file_path=str(jf.resolve()), size_bytes=jf.stat().st_size)
    log.info("meta_json_downloaded", file=str(jf.resolve()))

    # Enrich episode metadata from the freshly downloaded info.json.
    # Isolated: any failure logs a warning but never fails the job.
    try:
        report = enrich_episode_from_meta(session, episode)
        if report.fields_updated:
            log.info("enrich_meta.applied", episode_id=episode.id, fields=report.fields_updated)
        if report.note:
            log.debug("enrich_meta.note", episode_id=episode.id, note=report.note)
    except Exception:
        log.warning("enrich_meta.hook_failed", episode_id=episode.id, exc_info=True)

def _download_webpage(session, job: DownloadJob, episode: Episode, work: Work):
    if not episode.url:
        raise RuntimeError("Episode has no URL to fetch")

    paths = build_paths_for_episode(episode, work)
    out_dir = paths["base_dir"]
    _safe_mkdir(out_dir)
    html_path = out_dir / f"{paths['stem']}.html"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; audiobiblio/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(episode.url, timeout=30, headers=headers, allow_redirects=True)
    r.raise_for_status()

    ctype = r.headers.get("Content-Type", "")
    # We only want real HTML; skip saving if it's a playlist/json/etc.
    if "text/html" not in ctype:
        raise RuntimeError(f"Expected text/html, got Content-Type={ctype!r}")

    # Respect declared encoding; fallback to apparent_encoding → utf-8
    if not r.encoding:
        r.encoding = r.apparent_encoding or "utf-8"

    html_path.write_text(r.text, encoding="utf-8", errors="replace")
    _mark_asset_status(
        session, episode.id, AssetType.WEBPAGE, AssetStatus.COMPLETE,
        file_path=str(html_path.resolve()), size_bytes=html_path.stat().st_size
    )
    log.info("webpage_saved", file=str(html_path.resolve()), url=episode.url, content_type=ctype)

def run_pending_jobs(limit: int | None = None):
    s = get_session()
    # Sort by episode priority DESC (newer/more important first), then job ID ASC
    q = (s.query(DownloadJob)
         .join(Episode, Episode.id == DownloadJob.episode_id)
         .filter(DownloadJob.status == JobStatus.PENDING)
         .order_by(Episode.priority.desc(), DownloadJob.id.asc()))
    jobs = q.limit(limit).all() if limit else q.all()
    if not jobs:
        log.info("no_jobs")
        return 0

    done = 0
    since_pause = 0
    for job in jobs:
        # burst pacing: 5 jobs, then 10 s of quiet — robots get cut off
        if since_pause >= BURST_SIZE:
            time.sleep(BURST_PAUSE_S)
            since_pause = 0
        since_pause += 1
        ep = s.query(Episode).options(
            joinedload(Episode.work)
            .joinedload(Work.series)
            .joinedload(Series.program)
            .joinedload(Program.station),
        ).get(job.episode_id)
        if not ep:
            _update_job(s, job, JobStatus.ERROR, "Episode missing")
            continue
        work = ep.work
        if not work:
            _update_job(s, job, JobStatus.ERROR, "Work missing")
            continue

        if job.asset_type == AssetType.AUDIO and _day_quota_exhausted():
            log.info("daytime_audio_quota_reached", cap=DAY_HOURLY_AUDIO_CAP,
                     note="zbytek fronty pocka na noc / dalsi hodinu")
            break

        try:
            _update_job(s, job, JobStatus.RUNNING)
            if job.asset_type == AssetType.AUDIO:
                _download_audio(s, job, ep, work)
                _audio_done_at.append(time.time())
            elif job.asset_type == AssetType.META_JSON:
                _download_meta_json(s, job, ep, work)
            elif job.asset_type == AssetType.WEBPAGE:
                _download_webpage(s, job, ep, work)
            else:
                _update_job(s, job, JobStatus.SKIPPED, f"Unsupported asset {job.asset_type}")
                continue
            _update_job(s, job, JobStatus.SUCCESS)
            done += 1
        except Exception as e:
            log.error("download_failed", job_id=job.id, err=str(e))
            # also reflect on asset
            asset_type = job.asset_type
            _mark_asset_status(s, ep.id, asset_type, AssetStatus.FAILED)
            _update_job(s, job, JobStatus.ERROR, str(e))
    return done