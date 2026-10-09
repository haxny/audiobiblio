"""Small JSON state files next to the DB (crawl cursors, dirty flags).

Missing/corrupt file reads as {} — callers treat that as "start afresh".
Writes are atomic (tmp + rename).
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog

from audiobiblio.paths import get_dirs

log = structlog.get_logger()


def state_path(filename: str) -> Path:
    return get_dirs()["data"] / filename


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("crawl_state_unreadable", file=str(path), error=str(e))
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, state: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, sort_keys=True))
        tmp.replace(path)
    except OSError as e:
        log.warning("crawl_state_unwritable", file=str(path), error=str(e))


def load_json(filename: str) -> dict:
    return read_json(state_path(filename))


def save_json(filename: str, state: dict) -> None:
    write_json(state_path(filename), state)
