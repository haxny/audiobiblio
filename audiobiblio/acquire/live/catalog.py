"""A finished live recording enters the library like any download.

Station → Program → Series → Work → Episode (discovery_source
'live-recorder') + COMPLETE AUDIO asset, the file moved under the music
root (music stations) or the audiobooks root (spoken word), tags written
through the shared write_tags(), and the same values recorded as SCRAPED
provenance so the nightly DB→file tag sync has nothing to undo.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.acquire.live.detect import STATIONS, StationSpec, is_composition, norm
from audiobiblio.core.db.models import (
    Asset, AssetStatus, AssetType, Episode, FieldOrigin, LiveRecording, Program, Series, Station,
    Work,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.core.time import utcnow
from audiobiblio.tags.naming import sanitize_filename
from audiobiblio.tags.writer import write_tags

log = structlog.get_logger()

SOURCE = "live-recorder"
MAX_STEM = 80


def _roots() -> dict[str, Path]:
    from audiobiblio.core.config import load_config
    cfg = load_config()
    return {"music": Path(cfg.live_music_dir).expanduser(),
            "spoken": Path(cfg.library_dir).expanduser()}


def _spec(rec: LiveRecording) -> StationSpec:
    spec = STATIONS.get(rec.station_id)
    if spec is None:
        spec = next((s for s in STATIONS.values() if s.code == rec.station_code), None)
    return spec or StationSpec(rec.station_code, rec.station_code, "spoken", rec.stream_url,
                               "Záznamy z vysílání", "Záznamy z vysílání", "Záznamy z vysílání")


def composer_for(headline: str, items: list[dict]) -> str | None:
    """'Anton Bruckner' when a piece is 'Anton Bruckner: …' and the block's
    headline names him ('…svět Antona Brucknera'); otherwise None."""
    key = norm(headline)
    for it in items:
        for text in (it.get("title") or "", it.get("headline") or ""):
            if not is_composition(text):
                continue
            name = text.split(":", 1)[0].strip()
            surname = norm(name).split()[-1]
            stem = surname[:-2] if len(surname) > 6 else surname
            if stem and stem in key:
                return name
    return None


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def build_comment(description: str, items: list[dict], station_name: str) -> str:
    """Schedule description + when/where + playlist of the merged items."""
    parts = [description.strip()] if description and description.strip() else []
    if items:
        a, b = _local(items[0]["since"]), _local(items[-1]["till"])
        block = [f"{station_name}, {a.day}. {a.month}. {a.year} {a:%H:%M}–{b:%H:%M}", "Program:"]
        for it in items:
            s, e = _local(it["since"]), _local(it["till"])
            piece = (it.get("title") or "").strip().splitlines()[:1]
            head = (it.get("headline") or "").strip()
            text = head if not piece or piece[0] == head else f"{head} — {piece[0]}"
            block.append(f"{s:%H:%M}–{e:%H:%M} {text}")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def destination_for(kind: str, station_code: str, program_name: str) -> Path:
    spec = next((s for s in STATIONS.values() if s.code == station_code), None)
    root = _roots()["music" if spec and spec.kind == "music" else "spoken"]
    # names carry plain 'CRo' (channel digits only in the publisher tag)
    return root / f"{sanitize_filename(program_name)} (CRo)"


def _station(session: Session, spec: StationSpec) -> Station:
    st = session.query(Station).filter_by(code=spec.code).one_or_none()
    if st is None:
        st = Station(code=spec.code, name=spec.name)
        session.add(st)
        session.flush()
    return st


def _program(session: Session, station: Station, name: str) -> Program:
    """Reuse a program already known on the station ('Vikendove dopoledne
    na stanici d dur' for 'Víkendové dopoledne'), else create it."""
    key = norm(name)
    for p in session.query(Program).filter_by(station_id=station.id).all():
        if norm(p.name).startswith(key):
            return p
    p = Program(station_id=station.id, name=name)
    session.add(p)
    session.flush()
    return p


def _get_or_create(session: Session, model, **keys):
    row = session.query(model).filter_by(**keys).one_or_none()
    if row is None:
        row = model(**keys)
        session.add(row)
        session.flush()
    return row


def _ext_id(rec: LiveRecording) -> str:
    return f"live:{rec.station_code}:{hashlib.sha1(rec.dedupe_key.encode()).hexdigest()[:16]}"


def _stem(author: str | None, year: int, album: str, part: int | None) -> str:
    album_s = sanitize_filename(album)[:MAX_STEM].rstrip(". ")
    prefix = f"{sanitize_filename(author)} - ({year}) {album_s}" if author else f"({year}) {album_s}"
    return f"{prefix} - {part:02d}" if part is not None else prefix


def catalog_recording(session: Session, rec: LiveRecording, path: Path) -> Episode:
    """Turn the finished file at *path* into library rows + a tagged file."""
    path = Path(path)
    spec = _spec(rec)
    program_name = rec.program_name or (
        spec.block_program if rec.kind == "block" else spec.serial_program)
    station = _station(session, spec)
    program = _program(session, station, program_name)
    genre = program.genre or f"{unidecode(program_name)} ({spec.code})"
    series = _get_or_create(session, Series, program_id=program.id, name=rec.series_title[:400])
    work = _get_or_create(session, Work, series_id=series.id, title=rec.series_title[:500])
    year = rec.schedule_start.year
    composer = composer_for(rec.title, rec.items or []) if spec.kind == "music" else None
    if composer and not work.author:
        work.author = composer
    if rec.total_parts and work.expected_source != "manual":
        work.expected_total, work.expected_source = rec.total_parts, "schedule"
    comment = build_comment(rec.description or "", rec.items or [], spec.name)

    ep = session.query(Episode).filter_by(ext_id=_ext_id(rec)).one_or_none()
    if ep is None:
        ep = Episode(work_id=work.id, ext_id=_ext_id(rec), title=rec.title,
                     episode_number=rec.part, published_at=rec.schedule_start,
                     discovery_source=SOURCE, first_seen_at=utcnow(), last_seen_at=utcnow())
        session.add(ep)
        session.flush()
    rec.episode_id = ep.id

    asset = session.query(Asset).filter_by(episode_id=ep.id, type=AssetType.AUDIO).one_or_none()
    old = Path(asset.file_path) if asset is not None and asset.file_path else None
    if (asset is not None and asset.status == AssetStatus.COMPLETE and old and old.exists()
            and ((asset.extra or {}).get("coverage") or 0) >= (rec.coverage or 0)):
        log.info("live_catalog_kept_better", episode_id=ep.id, kept=str(old),
                 kept_coverage=(asset.extra or {}).get("coverage"), new_coverage=rec.coverage)
        return ep

    dest = destination_for(rec.kind, spec.code, program_name)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{_stem(work.author, year, work.title, rec.part)}{path.suffix.lower()}"
    if old and old.exists() and old != target:
        old.unlink()
    if target.exists():
        target.unlink()
    shutil.move(str(path), str(target))

    album_tags = {
        "album": unidecode(work.title), "artist": unidecode(work.author or ""),
        "albumartist": unidecode(work.author or ""), "genre": genre, "date": str(year),
        "publisher": f"{spec.code} {year}", "comment": comment,
    }
    if composer:
        album_tags["composer"] = unidecode(composer)
    track_tags = {"title": unidecode(rec.title),
                  "tracknumber": str(rec.part) if rec.part is not None else ""}
    write_tags(target, album_tags, track_tags)

    ep.title, ep.summary = rec.title, comment[:8000]
    ep.duration_ms = int((rec.duration_s or 0) * 1000) or None
    if asset is None:
        asset = Asset(episode_id=ep.id, type=AssetType.AUDIO)
        session.add(asset)
    asset.status = AssetStatus.COMPLETE
    asset.file_path = str(target)
    asset.size_bytes = target.stat().st_size
    asset.source_url = rec.stream_url
    asset.container = target.suffix.lstrip(".")
    asset.codec = "aac" if target.suffix.lower() in (".m4a", ".aac") else target.suffix.lstrip(".")
    asset.extra = {"live_recording_id": rec.id, "coverage": rec.coverage,
                   "window_start": rec.window_start.isoformat(), "stream_url": rec.stream_url}
    rec.file_path = str(target)
    _record_provenance(session, ep, work, {
        "title": rec.title, "genre": genre, "description": comment,
        "author": work.author, "year": str(year), "publisher": f"{spec.code} {year}"})
    session.flush()
    log.info("live_catalogued", episode_id=ep.id, work_id=work.id, file=str(target),
             coverage=rec.coverage, composer=composer)
    return ep


def _record_provenance(session: Session, ep: Episode, work: Work, values: dict) -> None:
    from audiobiblio.core.provenance import WORK_FIELDS
    for field, value in values.items():
        if not value:
            continue
        entity, eid = ("work", work.id) if field in WORK_FIELDS else ("episode", ep.id)
        record_value(session, entity, eid, field, value, FieldOrigin.SCRAPED, SOURCE)
