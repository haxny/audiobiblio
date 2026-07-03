"""
audiobiblio.library.mediainfo
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read technical audio metadata from a local file via mutagen and write the
fields to the corresponding Asset (and Episode.duration_ms if still NULL).

Public API
----------
read_media_info(path: Path) -> MediaInfo
    Returns a frozen dataclass with duration_ms, bitrate, channels,
    sample_rate, codec, and container.  All fields are ``None`` on any
    error — this function never raises.

apply_media_info(session, asset, path)
    Calls read_media_info() and writes the results to asset + episode.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: Optional[int]
    bitrate: Optional[int]
    channels: Optional[int]
    sample_rate: Optional[int]
    codec: Optional[str]
    container: Optional[str]


def _all_none() -> MediaInfo:
    return MediaInfo(
        duration_ms=None,
        bitrate=None,
        channels=None,
        sample_rate=None,
        codec=None,
        container=None,
    )


def read_media_info(path: Path) -> MediaInfo:
    """Read technical audio metadata from *path* using mutagen.

    Returns all-None MediaInfo on any error (unreadable, corrupt, wrong type).
    Never raises.
    """
    try:
        from mutagen import File as MFile  # type: ignore[import-untyped]

        mf = MFile(str(path))
        if mf is None or not hasattr(mf, "info"):
            return _all_none()

        info = mf.info

        # Duration
        raw_length = getattr(info, "length", None)
        duration_ms = int(raw_length * 1000) if raw_length is not None else None

        # Bitrate (mutagen reports kbps for most formats — keep as bps here)
        raw_bitrate = getattr(info, "bitrate", None)
        bitrate = int(raw_bitrate) if raw_bitrate is not None else None

        # Channels and sample rate
        raw_channels = getattr(info, "channels", None)
        channels = int(raw_channels) if raw_channels is not None else None

        raw_sr = getattr(info, "sample_rate", None)
        sample_rate = int(raw_sr) if raw_sr is not None else None

        # Codec — use the mutagen class name (e.g. "MP4", "MP3", "OggVorbis")
        codec: Optional[str] = type(mf).__name__ or None

        # Container — derive from file suffix, lower-cased, strip leading dot
        suffix = Path(path).suffix.lower().lstrip(".")
        container: Optional[str] = suffix if suffix else None

        return MediaInfo(
            duration_ms=duration_ms,
            bitrate=bitrate,
            channels=channels,
            sample_rate=sample_rate,
            codec=codec,
            container=container,
        )

    except Exception as exc:  # noqa: BLE001
        log.debug("mediainfo.read_failed", path=str(path), err=str(exc))
        return _all_none()


def apply_media_info(session, asset, path: Path) -> MediaInfo:
    """Read media info from *path* and write fields to *asset* + episode.

    - Fills asset.bitrate / .channels / .sample_rate / .codec / .container.
    - Sets episode.duration_ms if it is currently NULL.
    - Commits the session.
    - Returns the MediaInfo that was read.
    """
    info = read_media_info(path)

    asset.bitrate = info.bitrate
    asset.channels = info.channels
    asset.sample_rate = info.sample_rate
    asset.codec = info.codec
    asset.container = info.container

    if info.duration_ms is not None:
        # Lazy-load the episode relationship if needed
        episode = asset.episode
        if episode is not None and episode.duration_ms is None:
            episode.duration_ms = info.duration_ms

    session.commit()

    log.info(
        "mediainfo.applied",
        asset_id=asset.id,
        duration_ms=info.duration_ms,
        bitrate=info.bitrate,
        channels=info.channels,
        sample_rate=info.sample_rate,
        codec=info.codec,
        container=info.container,
    )
    return info
