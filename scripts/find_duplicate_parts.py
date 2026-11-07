"""List duplicate part rows — the same audio stored as two episodes. READ-ONLY.

2026-11-07: show-sweep (rAPI UUID) and the crawler/gap-fill path (numeric
yt-dlp id) each created a row for the same part of 'Dokumentarni povidky
...' — same croaod stream, same part, same duration — and both downloads
went into the same file. Ingest now resolves these to one episode; this
report lists the rows that already exist so the owner can repair them.

A pair is reported when two episodes of one work share
  * media_url      — the same direct stream/file URL, or
  * part_duration  — the same part number and a duration within 1 s
                     (not inside the catch-all 'mujrozhlas' program),
and, across works, when
  * same_file      — two COMPLETE audio assets point at the same file.

keep_id is the suggested survivor: the rAPI row (clean variant, rozhlas ad
rule), then the one with complete audio, then the older one.

The database is opened read-only (sqlite URI mode=ro); nothing is written.

Usage:
  python scripts/find_duplicate_parts.py /path/to/audiobiblio.db [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from audiobiblio.core.urls import is_media_url, norm_url

WORK_URL = "http://nasx:8321/works/{}"
PART_DURATION_TOLERANCE_MS = 1_000
CATCH_ALL_PROGRAMS = {"mujrozhlas"}
RAPI_SOURCES = {"show-sweep", "serial-sweep", "rapi-crawl"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_EPISODES_SQL = """
SELECT e.id, e.work_id, w.title, p.name, e.ext_id, e.title, e.episode_number,
       e.url, e.duration_ms, e.discovery_source,
       (SELECT a.file_path FROM assets a WHERE a.episode_id = e.id
          AND a.type = 'AUDIO' AND a.status = 'COMPLETE' LIMIT 1)
FROM episodes e
JOIN works w ON w.id = e.work_id
JOIN series se ON se.id = w.series_id
JOIN programs p ON p.id = se.program_id
"""
_FIELDS = ("id", "work_id", "work_title", "program", "ext_id", "title",
           "episode_number", "url", "duration_ms", "discovery_source", "file_path")


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open *path* read-only; raises FileNotFoundError when it is missing
    (mode=ro never creates a database)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)


def _is_rapi(ep: dict) -> bool:
    return (ep["discovery_source"] in RAPI_SOURCES
            or bool(ep["ext_id"] and _UUID_RE.match(ep["ext_id"])))


def _keeper_rank(ep: dict) -> tuple:
    return (_is_rapi(ep), ep["file_path"] is not None, -ep["id"])


def _pair_reasons(a: dict, b: dict) -> list[str]:
    reasons = []
    if a["work_id"] == b["work_id"]:
        if (is_media_url(a["url"]) and norm_url(a["url"]) == norm_url(b["url"])
                and (a["episode_number"] is None or b["episode_number"] is None
                     or a["episode_number"] == b["episode_number"])):
            reasons.append("media_url")
        if (a["episode_number"] is not None and a["episode_number"] == b["episode_number"]
                and a["duration_ms"] and b["duration_ms"]
                and abs(a["duration_ms"] - b["duration_ms"]) <= PART_DURATION_TOLERANCE_MS
                and (a["program"] or "").strip().lower() not in CATCH_ALL_PROGRAMS):
            reasons.append("part_duration")
    if a["file_path"] and a["file_path"] == b["file_path"]:
        reasons.append("same_file")
    return reasons


def _candidate_groups(episodes: list[dict]) -> list[list[dict]]:
    """Buckets that may hold duplicates — avoids comparing every pair."""
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for ep in episodes:
        if ep["episode_number"] is not None:
            buckets[("part", ep["work_id"], ep["episode_number"])].append(ep)
        if is_media_url(ep["url"]):
            buckets[("url", ep["work_id"], norm_url(ep["url"]))].append(ep)
        if ep["file_path"]:
            buckets[("file", ep["file_path"])].append(ep)
    return [g for g in buckets.values() if len(g) > 1]


def find_duplicate_pairs(path: str | Path) -> list[dict]:
    conn = connect_readonly(path)
    try:
        episodes = [dict(zip(_FIELDS, row)) for row in conn.execute(_EPISODES_SQL)]
    finally:
        conn.close()
    pairs: dict[frozenset, dict] = {}
    for group in _candidate_groups(episodes):
        for a, b in combinations(group, 2):
            key = frozenset((a["id"], b["id"]))
            if key in pairs:
                continue
            reasons = _pair_reasons(a, b)
            if not reasons:
                continue
            keep, dup = sorted((a, b), key=_keeper_rank, reverse=True)
            pairs[key] = {
                "work_id": keep["work_id"], "work_title": keep["work_title"],
                "episode_number": keep["episode_number"],
                "keep_id": keep["id"], "dup_id": dup["id"],
                "keep_ext_id": keep["ext_id"], "dup_ext_id": dup["ext_id"],
                "keep_duration_ms": keep["duration_ms"],
                "dup_duration_ms": dup["duration_ms"],
                "keep_file": keep["file_path"], "dup_file": dup["file_path"],
                "reasons": reasons,
            }
    return sorted(pairs.values(), key=lambda p: (p["work_id"], p["episode_number"] or 0,
                                                 p["keep_id"]))


def _fmt_s(ms: int | None) -> str:
    return f"{ms / 1000:.1f} s" if ms else "?"


def _print_report(pairs: list[dict]) -> None:
    if not pairs:
        print("No duplicate parts found.")
        return
    current = None
    for p in pairs:
        if p["work_id"] != current:
            current = p["work_id"]
            print(f"\n{p['work_title']}\n  {WORK_URL.format(p['work_id'])}")
        print(f"  part {p['episode_number']}: keep {p['keep_id']} "
              f"({p['keep_ext_id']}, {_fmt_s(p['keep_duration_ms'])}) | "
              f"dup {p['dup_id']} ({p['dup_ext_id']}, {_fmt_s(p['dup_duration_ms'])}) "
              f"[{', '.join(p['reasons'])}]")
    works = len({p["work_id"] for p in pairs})
    print(f"\n{len(pairs)} duplicate pair(s) in {works} work(s).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db", help="path to the audiobiblio SQLite database")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    try:
        pairs = find_duplicate_pairs(args.db)
    except FileNotFoundError as exc:
        print(f"database not found: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"cannot read database: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(pairs, ensure_ascii=False, indent=2))
    else:
        _print_report(pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
