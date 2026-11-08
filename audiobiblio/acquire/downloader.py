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
    if status in (JobStatus.SUCCESS, JobStatus.ERROR, JobStatus.SKIPPED, JobStatus.WATCH):
        job.finished_at = utcnow()
    if error:
        job.error = (job.error or "") + f"\n{error}"
    session.commit()

# Marker in job.error: the page is up but the media file is gone (404/410).
# process_watch_list() uses it to retry such jobs only rarely.
MEDIA_GONE_MARK = "[media-gone]"
_GONE_HTTP = (404, 410)


class MediaGone(RuntimeError):
    """The audio file itself returns 404/410 — removed at the source."""


def is_media_gone(exc: BaseException) -> bool:
    """True when a download failed because the source no longer has it."""
    if isinstance(exc, MediaGone):
        return True
    resp = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and resp is not None \
        and resp.status_code in _GONE_HTTP


def reset_orphaned_running() -> int:
    """Return RUNNING jobs to PENDING. Call once at startup: no download can
    be running yet, so any RUNNING row was cut off by a restart (deploy)."""
    s = get_session()
    jobs = s.query(DownloadJob).filter(DownloadJob.status == JobStatus.RUNNING).all()
    for job in jobs:
        job.status = JobStatus.PENDING
        job.started_at = None
        job.error = (job.error or "") + f"\nreset after restart at {utcnow()}"
    s.commit()
    if jobs:
        log.warning("orphaned_running_reset", count=len(jobs), job_ids=[j.id for j in jobs])
    return len(jobs)


def _mark_asset_status(session, episode_id: int, t: AssetType, status: AssetStatus, **fields):
    a = session.scalar(select(Asset).where(Asset.episode_id == episode_id, Asset.type == t))
    if not a:
        a = Asset(episode_id=episode_id, type=t)
        session.add(a)
    a.status = status
    for k, v in fields.items():
        setattr(a, k, v)
    session.commit()


_DIRECT_MEDIA_MARKERS = (".m3u8", ".mpd", "/manifest.mpd", "/playlist.m3u8")
_DIRECT_MEDIA_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac")


def _is_direct_media_url(url: str) -> bool:
    """True for URLs that point straight at audio (stream manifest or file),
    where yt-dlp's generic extractor is the downloader and entry-id selectors
    (--match-filter id=…, --playlist-items) must not be applied."""
    low = url.split("?", 1)[0].lower()
    return low.endswith(_DIRECT_MEDIA_EXTS) or any(m in low for m in _DIRECT_MEDIA_MARKERS)


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

    if _is_direct_media_url(url):
        # Direct stream/file URL IS the episode — yt-dlp's entry id there is
        # the stream stem, never our ext_id; any selector silently drops the
        # only entry (live incident: 10 revival jobs "succeeded" with no file).
        pass
    elif ext_id:
        base_args.insert(0, "--match-filter")
        base_args.insert(1, f"id={ext_id}")
    elif episode_number is not None:
        base_args.insert(0, "--playlist-items")
        base_args.insert(1, str(episode_number))

    cmd.extend(base_args)
    cmd.append(url)

    log.info("yt-dlp_command", command=" ".join(cmd))
    # 30min hard timeout: one stalled connection must never freeze the whole
    # runner (live incident: download hung 30+ min, queue looked "banned")
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1800)
    except subprocess.CalledProcessError as e:
        # str(e) drops stderr — keep yt-dlp's last lines, they hold the cause
        tail = "\n".join((e.stderr or e.output or "").strip().splitlines()[-5:])
        if any(f"HTTP Error {c}" in tail for c in _GONE_HTTP):
            raise MediaGone(tail) from e
        raise RuntimeError(f"yt-dlp exit {e.returncode}: {tail}") from e

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


class TargetOwnedElsewhere(Exception):
    """The target file is another episode's COMPLETE audio — downloading
    would overwrite it (duplicate rows of one part share a path)."""


_AUDIO_EXTS = (".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac")


def _target_owner(session, ep: Episode, base_dir: Path, stem: str) -> int | None:
    """Episode id of another episode whose COMPLETE audio sits at the path
    this download would write, else None."""
    targets = [str((base_dir / f"{stem}{ext}").resolve()) for ext in _AUDIO_EXTS]
    return session.scalar(
        select(Asset.episode_id).where(
            Asset.type == AssetType.AUDIO,
            Asset.status == AssetStatus.COMPLETE,
            Asset.episode_id != ep.id,
            Asset.file_path.in_(targets),
        ).limit(1)
    )


def _download_audio(session, job: DownloadJob, ep: Episode, work: Work):
    source = _audio_source_url(ep)
    if not source:
        raise RuntimeError("Episode has no URL to download")

    log.info("download_audio", url=source, episode=ep.id)

    paths = build_paths_for_episode(ep, work)

    # Never overwrite another episode's finished file (live 2026-11-07: the
    # gap-fill copy of a part overwrote the clean rAPI download of the same
    # part — two rows, one path). The duplicate row is for the owner to
    # merge (scripts/find_duplicate_parts.py), not for the downloader.
    owner = _target_owner(session, ep, paths["base_dir"], paths["stem"])
    if owner is not None:
        raise TargetOwnedElsewhere(
            f"target file belongs to episode {owner} (COMPLETE audio) — not overwriting")

    # Make sure the final directory exists
    _safe_mkdir(paths["base_dir"])

    asset_path = _run_ytdlp_audio(source, paths["base_dir"], paths["stem"],
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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

def _webpage_url(episode: Episode) -> str | None:
    """The episode's web page: its url, unless that is a bare audio/stream
    link (rAPI ingest, re-air revival) — then the first page-like alias
    (the station article the archive walk recorded)."""
    from audiobiblio.core.urls import page_url
    return page_url(episode.url, [a.url for a in episode.aliases])


def _audio_source_url(episode: Episode) -> str | None:
    """What yt-dlp downloads: the episode url when it is already a media link;
    when the url is the episode PAGE (show/serial sweep), the newest stream
    alias the sweep recorded next to it — the page itself only as fallback."""
    from audiobiblio.core.urls import is_media_url
    if not episode.url or is_media_url(episode.url):
        return episode.url
    streams = [a for a in (episode.aliases or []) if is_media_url(a.url)]
    if not streams:
        return episode.url
    return max(streams, key=lambda a: a.id or 0).url


class NoWebPage(Exception):
    """Episode has only an audio link — there is no page to save, ever."""


def _download_webpage(session, job: DownloadJob, episode: Episode, work: Work):
    page_url = _webpage_url(episode)
    if not page_url:
        raise NoWebPage("Episode has no web page URL (audio link only)")

    paths = build_paths_for_episode(episode, work)
    out_dir = paths["base_dir"]
    _safe_mkdir(out_dir)
    html_path = out_dir / f"{paths['stem']}.html"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; audiobiblio/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(page_url, timeout=30, headers=headers, allow_redirects=True)
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
    log.info("webpage_saved", file=str(html_path.resolve()), url=page_url, content_type=ctype)

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
        except TargetOwnedElsewhere as e:
            # SKIPPED asset is never re-planned: no nightly retry of a
            # download that could only destroy the other episode's copy
            log.warning("download_target_owned_elsewhere", job_id=job.id,
                        episode_id=ep.id, err=str(e))
            _mark_asset_status(s, ep.id, AssetType.AUDIO, AssetStatus.SKIPPED)
            _update_job(s, job, JobStatus.SKIPPED, str(e))
        except NoWebPage as e:
            # SKIPPED asset is never re-planned, so this job does not come back nightly
            _mark_asset_status(s, ep.id, AssetType.WEBPAGE, AssetStatus.SKIPPED)
            _update_job(s, job, JobStatus.SKIPPED, str(e))
        except Exception as e:
            if is_media_gone(e):
                # page may still list it, but the file is gone: watch, don't retry nightly
                log.warning("download_media_gone", job_id=job.id, episode_id=ep.id, err=str(e))
                _mark_asset_status(s, ep.id, job.asset_type, AssetStatus.FAILED)
                _update_job(s, job, JobStatus.WATCH, f"{MEDIA_GONE_MARK} {e}")
                continue
            log.error("download_failed", job_id=job.id, err=str(e))
            # also reflect on asset
            asset_type = job.asset_type
            _mark_asset_status(s, ep.id, asset_type, AssetStatus.FAILED)
            _update_job(s, job, JobStatus.ERROR, str(e))
    return done