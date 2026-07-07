from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, UniqueConstraint, Enum as SAEnum,
    BigInteger, JSON, Boolean, Index, Float
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class AssetType(str, Enum):
    AUDIO = "audio"
    META_JSON = "meta_json"
    WEBPAGE = "webpage"
    COVER = "cover"
    TRANSCRIPT = "transcript"
    SUBTITLE = "subtitle"
    OTHER = "other"

class AssetStatus(str, Enum):
    MISSING = "missing"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    FAILED = "failed"
    STALE = "stale"
    SKIPPED = "skipped"

class JobStatus(str, Enum):
    PENDING = "pending"
    APPROVAL = "approval"  # awaiting user approval before downloading
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    WATCH = "watch"  # failed download, monitoring for reappearance

class AvailabilityStatus(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    GONE = "gone"  # confirmed permanently removed

class CrawlTargetKind(str, Enum):
    STATION = "station"
    PROGRAM = "program"
    SERIES = "series"

class ApprovalMode(str, Enum):
    """Per-target policy for newly discovered episodes."""
    AUTO = "auto"      # queue downloads immediately (PENDING)
    REVIEW = "review"  # hold in Inbox until approved (APPROVAL)

class Station(Base):
    __tablename__ = "stations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    programs: Mapped[list["Program"]] = relationship(back_populates="station")

class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    ext_id: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    description: Mapped[Optional[str]] = mapped_column(String(4000))
    genre: Mapped[Optional[str]] = mapped_column(String(500))
    channel_label: Mapped[Optional[str]] = mapped_column(String(100))
    auto_crawl: Mapped[bool] = mapped_column(Boolean, default=False)
    crawl_interval_hours: Mapped[Optional[int]] = mapped_column(Integer, default=24)
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    station: Mapped[Station] = relationship(back_populates="programs")
    series_list: Mapped[list["Series"]] = relationship(back_populates="program")
    __table_args__ = (UniqueConstraint("station_id", "name", name="uq_program_per_station_name"),)

class Series(Base):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), index=True)
    ext_id: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(400), index=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    program: Mapped[Program] = relationship(back_populates="series_list")
    works: Mapped[list["Work"]] = relationship(back_populates="series")
    __table_args__ = (UniqueConstraint("program_id", "name", name="uq_series_per_program"),)

class Work(Base):
    """
    A concrete 'book/album' (many radio 'series' adapt one book -> multiple 'episodes').
    """
    __tablename__ = "works"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[Optional[str]] = mapped_column(String(500))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    asin: Mapped[Optional[str]] = mapped_column(String(50))
    extra: Mapped[Dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    series: Mapped[Series] = relationship(back_populates="works")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="work")
    __table_args__ = (UniqueConstraint("series_id", "title", name="uq_work_per_series"),)

class EpisodeAlias(Base):
    """Tracks alternate URLs/IDs for the same logical episode (re-airs, URL variants)."""
    __tablename__ = "episode_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000), index=True)
    ext_id: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    air_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    discovery_source: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    episode: Mapped["Episode"] = relationship(back_populates="aliases")
    __table_args__ = (
        UniqueConstraint("episode_id", "url", name="uq_alias_episode_url"),
    )


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    ext_id: Mapped[Optional[str]] = mapped_column(String(200), unique=True)
    title: Mapped[str] = mapped_column(String(600))
    episode_number: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    summary: Mapped[Optional[str]] = mapped_column(String(8000))
    # Availability tracking
    availability_status: Mapped[Optional[str]] = mapped_column(
        SAEnum(AvailabilityStatus), default=AvailabilityStatus.UNKNOWN, index=True
    )
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    discovery_source: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    work: Mapped[Work] = relationship(back_populates="episodes")
    assets: Mapped[list["Asset"]] = relationship(back_populates="episode")
    jobs: Mapped[list["DownloadJob"]] = relationship(back_populates="episode")
    aliases: Mapped[list["EpisodeAlias"]] = relationship(back_populates="episode")
    availability_logs: Mapped[list["AvailabilityLog"]] = relationship(back_populates="episode")
    upgrade_candidates: Mapped[list["UpgradeCandidate"]] = relationship(back_populates="episode")

    __table_args__ = (
        Index("ix_episode_work_num", "work_id", "episode_number"),
    )

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    type: Mapped[AssetType] = mapped_column(SAEnum(AssetType), index=True)
    status: Mapped[AssetStatus] = mapped_column(SAEnum(AssetStatus), default=AssetStatus.MISSING, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    file_path: Mapped[Optional[str]] = mapped_column(String(2000))
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    checksum: Mapped[Optional[str]] = mapped_column(String(128))
    codec: Mapped[Optional[str]] = mapped_column(String(80))
    container: Mapped[Optional[str]] = mapped_column(String(40))
    bitrate: Mapped[Optional[int]] = mapped_column(Integer)
    channels: Mapped[Optional[int]] = mapped_column(Integer)
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    extra: Mapped[Dict[str, Any] | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    episode: Mapped[Episode] = relationship(back_populates="assets")
    __table_args__ = (UniqueConstraint("episode_id", "type", name="uq_asset_per_episode_type"),)

class DownloadJob(Base):
    __tablename__ = "download_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType))
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), default=JobStatus.PENDING, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    command: Mapped[Optional[str]] = mapped_column(String(2000))
    error: Mapped[Optional[str]] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    episode: Mapped[Episode] = relationship(back_populates="jobs")

class CrawlTarget(Base):
    __tablename__ = "crawl_targets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    kind: Mapped[CrawlTargetKind] = mapped_column(SAEnum(CrawlTargetKind), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    approval_mode: Mapped[ApprovalMode] = mapped_column(
        SAEnum(ApprovalMode), default=ApprovalMode.REVIEW,
        server_default="REVIEW", nullable=False,
    )
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_crawl_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class CatalogStatus(str, Enum):
    MISSING = "missing"
    MATCHED_DB = "matched_db"
    MATCHED_FILE = "matched_file"
    DOWNLOADED = "downloaded"


class CatalogEntry(Base):
    __tablename__ = "catalog_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), index=True)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(600))
    author: Mapped[Optional[str]] = mapped_column(String(500))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    air_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(100))  # "wikipedia", "mluvenypanacek", "manual"
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    episode_id: Mapped[Optional[int]] = mapped_column(ForeignKey("episodes.id"), index=True)
    local_file: Mapped[Optional[str]] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(
        String(50), default=CatalogStatus.MISSING, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    program: Mapped[Program] = relationship()
    episode: Mapped[Optional[Episode]] = relationship()

    __table_args__ = (
        UniqueConstraint("program_id", "episode_number", "title", name="uq_catalog_entry"),
    )


class CdwifiDownload(Base):
    """Tracks files downloaded from CD WiFi (cdwifi.cz) train portal."""
    __tablename__ = "cdwifi_downloads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)  # "audiobook", "music", "video"
    source_id: Mapped[str] = mapped_column(String(200))  # portal item ID
    title: Mapped[str] = mapped_column(String(600))
    author: Mapped[Optional[str]] = mapped_column(String(500))
    track_number: Mapped[Optional[int]] = mapped_column(Integer)
    track_title: Mapped[Optional[str]] = mapped_column(String(600))
    source_url: Mapped[str] = mapped_column(String(2000))  # portal file path
    file_path: Mapped[Optional[str]] = mapped_column(String(2000))  # local download path
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(50), default="complete", index=True)
    extra: Mapped[Dict[str, Any] | None] = mapped_column(JSON)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("source", "source_id", "source_url", name="uq_cdwifi_download"),
    )


class TorrentStatus(str, Enum):
    NEW = "new"              # scraped, not yet matched
    MATCHED = "matched"      # matched to local file on NAS
    QUEUED = "queued"        # sent to Download Station
    DOWNLOADED = "downloaded" # confirmed downloaded
    SKIPPED = "skipped"      # user decided to skip


class TorrentEntry(Base):
    """Torrent catalog entry scraped from sktorrent.eu."""
    __tablename__ = "torrent_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    info_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1000))
    category: Mapped[str] = mapped_column(String(100), index=True)  # "knihy", "hudba", etc.
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    size_display: Mapped[Optional[str]] = mapped_column(String(50))  # "1.2 GB" as shown
    seeders: Mapped[Optional[int]] = mapped_column(Integer)
    leechers: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    detail_url: Mapped[Optional[str]] = mapped_column(String(2000))
    torrent_url: Mapped[Optional[str]] = mapped_column(String(2000))  # .torrent download link
    uploader: Mapped[Optional[str]] = mapped_column(String(200))
    # Parsed metadata
    author: Mapped[Optional[str]] = mapped_column(String(500))
    language: Mapped[Optional[str]] = mapped_column(String(50))
    format: Mapped[Optional[str]] = mapped_column(String(50))  # "mp3", "epub", "pdf", etc.
    extra: Mapped[Dict[str, Any] | None] = mapped_column(JSON)
    # Matching & download tracking
    status: Mapped[str] = mapped_column(
        String(50), default=TorrentStatus.NEW, index=True
    )
    matched_path: Mapped[Optional[str]] = mapped_column(String(2000))  # NAS path if matched
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AvailabilityLog(Base):
    __tablename__ = "availability_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    was_available: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    episode: Mapped[Episode] = relationship(back_populates="availability_logs")


class UpgradeStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    STAGED = "staged"
    REPLACED = "replaced"
    KEPT_OLD = "kept_old"
    DISMISSED = "dismissed"


class UpgradeCandidate(Base):
    """A potential quality upgrade for an owned episode detected during re-air ingestion.

    Created by evaluate_reair() when a re-aired URL is found for an already-owned episode
    and the duration difference exceeds the ad-suspect threshold (>5 000 ms), or when the
    candidate duration is unknown. Never auto-resolved — always requires human review
    (spec §4.2 AD RULE).
    """
    __tablename__ = "upgrade_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    candidate_url: Mapped[str] = mapped_column(String(1000))
    candidate_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    owned_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    owned_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"))
    status: Mapped[UpgradeStatus] = mapped_column(
        SAEnum(UpgradeStatus), default=UpgradeStatus.PENDING_REVIEW, index=True
    )
    staged_path: Mapped[Optional[str]] = mapped_column(String(2000))
    note: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    episode: Mapped["Episode"] = relationship(back_populates="upgrade_candidates")
    owned_asset: Mapped[Optional["Asset"]] = relationship(foreign_keys=[owned_asset_id])

    __table_args__ = (
        UniqueConstraint("episode_id", "candidate_url", name="uq_upgrade_candidate"),
    )


class ImportBucket(str, Enum):
    """Classification bucket for an ImportFinding."""
    MATCHED = "matched"      # file matched to a single episode
    DUPLICATE = "duplicate"  # matched episode already has a COMPLETE audio at a different path
    UNKNOWN = "unknown"      # no match, or multiple ambiguous candidates
    CONFLICT = "conflict"    # reserved for future manual-resolution flows


class FieldOrigin(str, Enum):
    """Where a metadata value came from — precedence: MANUAL > ENRICHED > FILE > SCRAPED."""
    SCRAPED = "scraped"    # source website / feed metadata
    FILE = "file"          # read from existing file tags
    ENRICHED = "enriched"  # external enrichment (databazeknih, RAPI)
    MANUAL = "manual"      # user-edited; never overwritten automatically


class MetadataValue(Base):
    """One observed value for one metadata field of one entity, with provenance.

    The DB is the source of truth (spec §2): file tags are projections.
    Current effective value = provenance.resolve_field() over an entity's rows.
    """
    __tablename__ = "metadata_values"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)  # "work" | "episode"
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(50))  # "title", "author", "narrator", ...
    value: Mapped[Optional[str]] = mapped_column(String(4000))
    origin: Mapped[FieldOrigin] = mapped_column(SAEnum(FieldOrigin), index=True)
    source: Mapped[str] = mapped_column(String(100))  # "mujrozhlas", "databazeknih", "user", file path...
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field", "origin", "source",
                         name="uq_metadata_value_provenance"),
        Index("ix_metadata_values_entity_field", "entity_type", "entity_id", "field"),
    )


class ImportFinding(Base):
    """One file discovered by the import scanner, pending human review.

    path is unique — re-scanning updates the "new" row rather than creating a duplicate.
    Findings with status "accepted" or "ignored" are never re-opened by a re-scan.
    """
    __tablename__ = "import_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    path: Mapped[str] = mapped_column(String(2000), unique=True)
    bucket: Mapped[ImportBucket] = mapped_column(SAEnum(ImportBucket), index=True)
    episode_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("episodes.id"), nullable=True, index=True
    )
    details: Mapped[Dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    episode: Mapped[Optional["Episode"]] = relationship()
