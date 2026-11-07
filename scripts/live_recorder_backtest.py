"""Backtest the live-recorder detection rules on a saved schedule dump.

Usage:
    uv run python scripts/live_recorder_backtest.py DUMP.json \
        [--stations d-dur,vltava,plus] [--avail AVAIL.json] [--kbps 192]

DUMP.json is the flattened audit format (list of dicts with keys id, since,
till, title, show, station, show_id, ep, desc, audio). AVAIL.json (optional)
is a read-only export of what is already available — {"auto": [active AUTO
crawl-target names], "eps": [[work title, program, episode title, part], …]}
— fed through the same matcher the planner uses. Prints what the recorder
would have recorded: primaries vs backups, hours and GB.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from audiobiblio.acquire.live.detect import (
    DEFAULT_RULES, STATION_ALIASES, STATIONS, assign_roles, detect,
)
from audiobiblio.acquire.live.planner import make_known
from audiobiblio.acquire.live.schedule import ScheduleItem


def _known(path: Path | None):
    if path is None:
        return None
    data = json.loads(path.read_text())
    by_part: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for wt, pn, et, part in data["eps"]:
        by_part[int(part)].append((wt or "", pn or "", et or ""))
    return make_known(data["auto"], lambda part: by_part.get(part, []))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--stations", default="d-dur,vltava,plus")
    ap.add_argument("--avail", type=Path)
    ap.add_argument("--kbps", type=int, default=192, help="measured D-dur AAC stream ≈ 192")
    args = ap.parse_args()
    items = [ScheduleItem.from_flat(d) for d in json.loads(args.dump.read_text())]
    known = _known(args.avail)
    span_days = (max(i.since for i in items) - min(i.since for i in items)).days + 1
    tot_h = tot_gb = 0.0
    for name in args.stations.split(","):
        sid = STATION_ALIASES[name.strip()]
        wins = detect(items, {sid: DEFAULT_RULES[sid]}, known=known)
        roles = assign_roles(wins)
        prim = [w for w, r in roles if r == "planned"]
        hours = sum(w.duration_s for w in prim) / 3600
        gb = hours * 3600 * args.kbps * 1000 / 8 / 1e9
        tot_h, tot_gb = tot_h + hours, tot_gb + gb
        print(f"\n== {STATIONS[sid].code} ({', '.join(DEFAULT_RULES[sid])}) — {span_days} days: "
              f"{len(prim)} recordings, {len(wins) - len(prim)} backups, {hours:.1f} h, "
              f"{gb:.2f} GB; per 30 days ≈ {gb * 30 / span_days:.2f} GB")
        for w in prim:
            print(f"  {w.schedule_start:%a %Y-%m-%d %H:%M}–{w.schedule_end:%H:%M} "
                  f"{w.kind:6} [{w.program}] {w.title[:80]}")
        print("  by kind:", dict(Counter(w.kind for w in prim)))
    print(f"\nTOTAL {tot_h:.1f} h, {tot_gb:.2f} GB in {span_days} days; "
          f"≈ {tot_gb * 30 / span_days:.2f} GB / 30 days at {args.kbps} kbps")


if __name__ == "__main__":
    main()
