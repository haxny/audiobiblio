from __future__ import annotations
import shutil, subprocess, sys, time
from pathlib import Path
import structlog
import requests
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from .db.models import DownloadJob, JobStatus, AssetType, AssetStatus, Episode, Asset, Work, Series, Program, Station
from .db.session import get_session
from .pipelines.library import build_paths_for_episode
from .pipelines.postprocess import tag_audio

log = structlog.get_logger()

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
        job.started_at = job.started_at or __import__("datetime").datetime.utcnow()
    if status in (JobStatus.SUCCESS, JobStatus.ERROR, JobStatus.SKIPPED):
        job.finished_at = __import__("datetime").datetime.utcnow()
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


def _download_audio(session, job: DownloadJob, ep: Episode, work: Work):
    if not ep.url:
        raise RuntimeError("Episode has no URL to download")

    log.info("download_audio", url=ep.url, episode=ep.id)

    paths = build_paths_for_episode(ep, work)

    # Make sure the final directory exists
    _safe_mkdir(paths["base_dir"])

    # This is the custom output template for yt-dlp
    output_template = str(paths["base_dir"] / f"{paths['stem']}.%(ext)s")

    cmd = _yt_dlp_cmd()
    if not cmd:
        raise RuntimeError("yt-dlp is not installed or importable")

    # Build base command — tags written by shared tags package after download
    base_args = [
        "--extract-audio",
        "--audio-format", "m4a",
        "--audio-quality", "0",
        "--embed-thumbnail",
        "--no-download-archive",
        "--output", output_template,
    ]

    if ep.episode_number is not None:
        base_args.insert(0, "--playlist-items")
        base_args.insert(1, str(ep.episode_number))

    cmd.extend(base_args)
    cmd.append(ep.url)

    log.info("yt-dlp_command", command=" ".join(cmd))

    p = subprocess.run(cmd, capture_output=True, text=True, check=True)

    # Locate the actual output file (extension may differ from template)
    expected = Path(output_template.replace("%(ext)s", "m4a"))
    asset_path = expected
    if not expected.exists():
        # Scan for any audio file matching the stem
        candidates = list(expected.parent.glob(f"{expected.stem}.*"))
        audio_exts = {".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac"}
        candidates = [c for c in candidates if c.suffix.lower() in audio_exts]
        if candidates:
            asset_path = candidates[0]
        else:
            raise RuntimeError(f"Download succeeded but output file not found: {expected}")

    # Write metadata tags via shared tags package (genre taxonomy, all formats)
    tag_audio(asset_path, ep, work)

    _mark_asset_status(session, ep.id, AssetType.AUDIO, AssetStatus.COMPLETE,
                       file_path=str(asset_path.resolve()), size_bytes=asset_path.stat().st_size)

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
    for job in jobs:
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

        try:
            _update_job(s, job, JobStatus.RUNNING)
            if job.asset_type == AssetType.AUDIO:
                _download_audio(s, job, ep, work)
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