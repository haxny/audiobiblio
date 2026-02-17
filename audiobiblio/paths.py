from __future__ import annotations
from pathlib import Path
from platformdirs import PlatformDirs

APP = "audiobiblio"
AUTHOR = "audiobiblio"


def get_dirs() -> dict[str, Path]:
    d = PlatformDirs(appname=APP, appauthor=AUTHOR, roaming=True)
    paths = {
        "data": Path(d.user_data_dir),     # long-lived data (DB etc.)
        "config": Path(d.user_config_dir), # config files
        "cache": Path(d.user_cache_dir),   # temp caches
        "state": Path(d.user_state_dir),   # logs/run state
        "logs": Path(d.user_log_dir),      # logs (separate if supported)
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths
