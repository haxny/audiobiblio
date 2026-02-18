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
    library_dir: str = ""  # empty = use platformdirs default
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

    # Rate limiting
    rate_limit_rps: float = 0.5  # requests per second for mujrozhlas.cz


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
        "AUDIOBIBLIO_RATE_LIMIT": "rate_limit_rps",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            field_type = type(getattr(cfg, attr))
            if field_type == int:
                setattr(cfg, attr, int(val))
            elif field_type == float:
                setattr(cfg, attr, float(val))
            else:
                setattr(cfg, attr, val)

    return cfg
