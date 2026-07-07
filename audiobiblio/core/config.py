"""
config — Loads config.yaml with env var overrides.

Precedence: env vars > config.yaml > defaults
"""
from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass, field
import yaml


@dataclass
class Config:
    # Database
    db_url: str = ""  # empty = use default SQLite path

    # Library paths
    library_dir: str = "~/Downloads/audiobiblio"
    download_dir: str = "media/_downloading"

    # Scheduler intervals (minutes)
    crawl_interval_minutes: int = 60
    download_interval_minutes: int = 5

    # Audiobookshelf
    abs_url: str = ""
    abs_api_key: str = ""

    # JDownloader
    jd_host: str = "localhost"
    jd_port: int = 3129

    # Web server
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    # Download
    download_batch_size: int = 10  # max jobs per scheduler cycle

    # Rate limiting
    rate_limit_rps: float = 0.5  # requests per second for mujrozhlas.cz

    # Trash retention
    trash_retention_days: int = 30

    # Import scanner inbox directories (comma-separated in env)
    inbox_dirs: list = field(default_factory=list)


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config from YAML file, then override with env vars."""
    cfg = Config()

    # 1. Load from YAML if available
    if config_path is None:
        config_path = os.environ.get("AUDIOBIBLIO_CONFIG", "config.yaml")
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for key, value in data.items():
            key_norm = key.replace("-", "_")
            if hasattr(cfg, key_norm) and value is not None:
                setattr(cfg, key_norm, value)

    # 2. Override with env vars (AUDIOBIBLIO_ prefix)
    #
    # Fields listed in _DEDICATED_ENV_FIELDS are handled by their own parsers
    # below (e.g. inbox_dirs needs comma-split into a list).  Exclude them here
    # so they are never written by the generic scalar loop — prevents a future
    # accidental addition to env_map from silently setting the wrong type.
    _DEDICATED_ENV_FIELDS: frozenset = frozenset({"inbox_dirs"})

    env_map = {
        "AUDIOBIBLIO_DB_URL": "db_url",
        "AUDIOBIBLIO_LIBRARY_DIR": "library_dir",
        "AUDIOBIBLIO_DOWNLOAD_DIR": "download_dir",
        "AUDIOBIBLIO_CRAWL_INTERVAL": "crawl_interval_minutes",
        "AUDIOBIBLIO_DOWNLOAD_INTERVAL": "download_interval_minutes",
        "ABS_URL": "abs_url",
        "ABS_API_KEY": "abs_api_key",
        "JD_HOST": "jd_host",
        "JD_PORT": "jd_port",
        "AUDIOBIBLIO_WEB_HOST": "web_host",
        "AUDIOBIBLIO_WEB_PORT": "web_port",
        "AUDIOBIBLIO_DOWNLOAD_BATCH_SIZE": "download_batch_size",
        "AUDIOBIBLIO_RATE_LIMIT": "rate_limit_rps",
        "AUDIOBIBLIO_TRASH_RETENTION_DAYS": "trash_retention_days",
    }
    for env_key, attr in env_map.items():
        if attr in _DEDICATED_ENV_FIELDS:
            continue  # handled by dedicated parser below — skip generic scalar write
        val = os.environ.get(env_key)
        if val is not None:
            field_type = type(getattr(cfg, attr))
            if field_type == int:
                setattr(cfg, attr, int(val))
            elif field_type == float:
                setattr(cfg, attr, float(val))
            else:
                setattr(cfg, attr, val)

    # inbox_dirs: comma-separated env var, list in YAML
    inbox_env = os.environ.get("AUDIOBIBLIO_INBOX_DIRS")
    if inbox_env is not None:
        cfg.inbox_dirs = [d.strip() for d in inbox_env.split(",") if d.strip()]

    return cfg
