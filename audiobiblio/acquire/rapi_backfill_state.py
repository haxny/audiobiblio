"""Per-show backfill cursor for the rAPI crawl.

A crawl reads at most HEAD_PAGES of a show's live episodes (newest first).
Every show's deeper live history (Čelisti: 686 episodes back to 2011) is
then read a few pages per crawl from where the last walk stopped, once,
until the end — DONE marks a finished show. The cursor survives container
restarts as a small JSON file next to the DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog

from audiobiblio.paths import get_dirs

log = structlog.get_logger()

_FILENAME = "rapi_backfill.json"
DONE = -1  # whole live history has been read


def _path() -> Path:
    return get_dirs()["data"] / _FILENAME


def load() -> dict[str, int]:
    """show_uuid → next offset to read (or DONE). Missing/corrupt file =
    every show starts its backfill afresh (idempotent: known episodes skip)."""
    try:
        data = json.loads(_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("rapi_backfill_state_unreadable", error=str(e))
        return {}
    return {k: int(v) for k, v in data.items() if isinstance(v, int)}


def save(state: dict[str, int]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, sort_keys=True))
        tmp.replace(path)  # atomic: a crash never leaves half a file
    except OSError as e:
        log.warning("rapi_backfill_state_unwritable", error=str(e))
