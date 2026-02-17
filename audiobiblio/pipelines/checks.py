from __future__ import annotations
from typing import Iterable
from sqlalchemy import select
from ..db.models import Asset, AssetType, AssetStatus, Episode, DownloadJob, JobStatus
from ..db.session import get_session
import structlog

log = structlog.get_logger()

REQUIRED_ASSETS: list[AssetType] = [AssetType.META_JSON, AssetType.WEBPAGE, AssetType.AUDIO]

def ensure_assets_for_episode(session, episode_id: int) -> list[Asset]:
    """Upsert required asset rows for an episode and return them."""
    assets = {a.type: a for a in session.scalars(select(Asset).where(Asset.episode_id == episode_id)).all()}
    changed = False
    for t in REQUIRED_ASSETS:
        if t not in assets:
            a = Asset(episode_id=episode_id, type=t, status=AssetStatus.MISSING)
            session.add(a)
            assets[t] = a
            changed = True
    if changed:
        session.commit()
    return list(assets.values())

def plan_downloads(session, episode_id: int) -> list[DownloadJob]:
    """Consult assets and create DownloadJob rows only for what is needed."""
    jobs: list[DownloadJob] = []
    assets = ensure_assets_for_episode(session, episode_id)
    for a in assets:
        need = a.status in {AssetStatus.MISSING, AssetStatus.STALE, AssetStatus.FAILED}
        if need:
            job = DownloadJob(episode_id=episode_id, asset_type=a.type, status=JobStatus.PENDING,
                              reason=f"asset:{a.type} status {a.status}")
            session.add(job)
            jobs.append(job)
    if jobs:
        session.commit()
        log.info("planned_downloads", episode_id=episode_id, count=len(jobs))
    else:
        log.info("nothing_to_do", episode_id=episode_id)
    return jobs

def mark_asset_complete(session, episode_id: int, asset_type: AssetType, file_path: str,
                        size_bytes: int | None = None, extra: dict | None = None):
    a = session.scalar(
        select(Asset).where(Asset.episode_id == episode_id, Asset.type == asset_type)
    )
    if not a:
        a = Asset(episode_id=episode_id, type=asset_type)
        session.add(a)
    a.status = AssetStatus.COMPLETE
    a.file_path = file_path
    a.size_bytes = size_bytes
    if extra:
        a.extra = (a.extra or {}) | extra
    session.commit()
    log.info("asset_complete", episode_id=episode_id, asset=str(asset_type), path=file_path)