"""Inventory of book candidates — one folder of audio files = one candidate;
loose files in a root or an "Author [audio]" folder are one candidate each.

Stored in its own SQLite file (never locks the main DB). Incremental: a
candidate is re-read only when its mtime or file count changed.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".mpga", ".ogg", ".opus", ".flac", ".aac", ".wma"}
SKIP_DIRS = {"@eaDir", "#recycle", "_meta", ".AppleDouble"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  path TEXT PRIMARY KEY, root TEXT, kind TEXT, n_audio INTEGER, bytes INTEGER,
  duration_s REAL, album TEXT, artist TEXT, date TEXT, genre TEXT,
  mtime REAL, scanned_at REAL
);
CREATE INDEX IF NOT EXISTS ix_cand_root ON candidates(root);
"""


@dataclass(frozen=True)
class Candidate:
    path: str
    root: str
    kind: str            # "dir" | "file"
    files: tuple[str, ...]


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    return con


def _is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXT and not name.startswith(".")


def iter_candidates(root: Path):
    """Yield candidates under one root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        audio = sorted(f for f in filenames if _is_audio(f) and ".cmdr-tmp" not in f)
        if not audio:
            continue
        here = Path(dirpath)
        loose = here == root or here.name.endswith("[audio]")
        if loose:
            for f in audio:
                yield Candidate(str(here / f), str(root), "file", (str(here / f),))
        else:
            yield Candidate(str(here), str(root), "dir", tuple(str(here / f) for f in audio))


def _measure(files: tuple[str, ...]) -> dict:
    from mutagen import File as MFile
    total, size = 0.0, 0
    tags: dict[str, Counter] = {k: Counter() for k in ("album", "artist", "date", "genre")}
    for f in files:
        try:
            size += os.path.getsize(f)
            m = MFile(f, easy=True)
        except Exception:
            continue
        if m is None:
            continue
        total += getattr(m.info, "length", 0) or 0
        for k in tags:
            v = (m.get(k) or [None])[0] if hasattr(m, "get") else None
            if v:
                tags[k][str(v)] += 1
    top = {k: (c.most_common(1)[0][0] if c else None) for k, c in tags.items()}
    return {"bytes": size, "duration_s": round(total, 1), **top}


def scan_root(con: sqlite3.Connection, root: Path) -> dict:
    """(Re)inventory one root; returns counts."""
    seen, fresh = 0, 0
    known = {p: (m, n) for p, m, n in con.execute(
        "select path, mtime, n_audio from candidates where root = ?", (str(root),))}
    present = set()
    for c in iter_candidates(root):
        seen += 1
        present.add(c.path)
        try:
            mtime = os.path.getmtime(c.path)
        except OSError:
            continue
        if known.get(c.path) == (mtime, len(c.files)):
            continue
        m = _measure(c.files)
        con.execute("""insert or replace into candidates
            (path, root, kind, n_audio, bytes, duration_s, album, artist, date, genre, mtime, scanned_at)
            values (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.path, c.root, c.kind, len(c.files), m["bytes"], m["duration_s"], m["album"],
             m["artist"], m["date"], m["genre"], mtime, time.time()))
        fresh += 1
        if fresh % 200 == 0:
            con.commit()
            log.info("robot_inventory_progress", root=str(root), seen=seen, measured=fresh)
    gone = [p for p in known if p not in present]
    con.executemany("delete from candidates where path = ?", [(p,) for p in gone])
    con.commit()
    return {"root": str(root), "candidates": seen, "measured": fresh, "removed": len(gone)}
