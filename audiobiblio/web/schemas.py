"""
schemas — Pydantic request/response models for the API.
"""
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


# --- System ---

class HealthResponse(BaseModel):
    status: str
    scheduler_running: bool


class StatsResponse(BaseModel):
    episodes_total: int
    episodes_available: int
    episodes_gone: int
    jobs_total: int
    jobs_pending: int
    jobs_error: int
    jobs_success: int
    targets_total: int
    targets_active: int
    last_crawl: datetime | None
    last_download: datetime | None


# --- Jobs ---

class JobResponse(BaseModel):
    id: int
    episode_id: int
    episode_title: str
    work_title: str
    asset_type: str
    status: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    class Config:
        from_attributes = True


class PaginatedJobs(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


# --- Episodes ---

class EpisodeResponse(BaseModel):
    id: int
    title: str
    work_title: str
    series_name: str
    program_name: str
    url: str | None
    episode_number: int | None
    availability_status: str | None
    audio_status: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class EpisodeDetailResponse(EpisodeResponse):
    summary: str | None
    duration_ms: int | None
    published_at: datetime | None
    assets: list[AssetResponse]
    jobs: list[JobResponse]


class AssetResponse(BaseModel):
    id: int
    type: str
    status: str
    file_path: str | None
    source_url: str | None

    class Config:
        from_attributes = True


class PaginatedEpisodes(BaseModel):
    items: list[EpisodeResponse]
    total: int
    limit: int
    offset: int


# --- Targets ---

class TargetResponse(BaseModel):
    id: int
    url: str
    kind: str
    name: str | None
    active: bool
    interval_hours: int
    last_crawled_at: datetime | None
    next_crawl_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class TargetCreateRequest(BaseModel):
    url: str
    kind: str = "program"
    name: str | None = None
    interval_hours: int = 24


class TargetUpdateRequest(BaseModel):
    active: bool | None = None
    interval_hours: int | None = None
    name: str | None = None


# --- Ingest ---

class IngestProgramRequest(BaseModel):
    url: str
    genre: str = ""
    skip_ajax: bool = False
    channel_label: str = ""


class IngestPreviewResponse(BaseModel):
    raw_count: int
    unique_count: int
    reairs: int
    already_in_db: int
    episodes: list[dict]


class IngestUrlRequest(BaseModel):
    url: str


# --- Background tasks ---

class TaskResponse(BaseModel):
    task_id: str
    name: str
    status: str


# Fix forward reference
EpisodeDetailResponse.model_rebuild()
