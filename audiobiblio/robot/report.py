"""Phase-1 report: what the robot WOULD do (no moves).

  python -m audiobiblio.robot.report            # inventory + report
  SKIP_SCAN=1 python -m audiobiblio.robot.report # report from the last inventory
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from audiobiblio.paths import get_dirs
from audiobiblio.robot.inventory import connect, scan_root
from audiobiblio.robot.keys import classify_pair, work_key

EBOOKS = Path(os.environ.get("EBOOKS_ROOT", "/media/ebooks"))
WORK_ROOTS = ["eBOOKs.temp", "eBOOKs.temp2sort", "eBOOKs.temp2sort2025",
              "eBOOKs.temp2sort.ZV", "2sort", "audiobooks/_mac_import"]
SHELF_ROOTS = ["eBOOKs.fiction", "4kids", "eBOOKs.nonfiction"]


def _key(row: sqlite3.Row) -> str:
    return work_key(row["artist"], row["album"], Path(row["path"]).name)


def build_report(con: sqlite3.Connection, out_dir: Path) -> dict:
    con.row_factory = sqlite3.Row
    rows = con.execute("select * from candidates").fetchall()
    shelf_roots = {str(EBOOKS / r) for r in SHELF_ROOTS}
    shelf, work = defaultdict(list), defaultdict(list)
    for r in rows:
        (shelf if r["root"] in shelf_roots else work)[_key(r)].append(r)
    decisions, counts, gb = [], Counter(), Counter()
    for key, cands in work.items():
        on_shelf = shelf.get(key, [])
        for r in cands:
            if key.endswith("|") or len(key.split("|")[1]) < 3:
                verdict, ref = "unidentified", ""
            elif on_shelf:
                kinds = {classify_pair(r["duration_s"], s["duration_s"]) for s in on_shelf}
                verdict = "copy_of_shelved" if "same" in kinds else (
                    "other_version" if "version" in kinds else "shelved_length_unknown")
                ref = on_shelf[0]["path"]
            elif len(cands) > 1:
                verdict, ref = "duplicate_in_workdirs", cands[0]["path"] if r is not cands[0] else ""
            else:
                verdict, ref = "new", ""
            counts[verdict] += 1
            gb[verdict] += (r["bytes"] or 0) / 1e9
            decisions.append([verdict, r["root"].replace(str(EBOOKS) + "/", ""), r["path"].replace(str(EBOOKS) + "/", ""),
                              r["n_audio"], round((r["bytes"] or 0) / 1e6, 1),
                              round((r["duration_s"] or 0) / 60, 1), key, ref.replace(str(EBOOKS) + "/", "")])
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "robot_report.tsv").open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["verdict", "root", "path", "files", "MB", "minutes", "key", "matches"])
        w.writerows(sorted(decisions, key=lambda d: (d[0], d[6])))
    by_root = Counter((d[1], d[0]) for d in decisions)
    return {"counts": dict(counts), "GB": {k: round(v, 1) for k, v in gb.items()},
            "shelf_works": len(shelf), "by_root": by_root}


def main() -> None:
    db = get_dirs()["data"] / "robot.sqlite3"
    con = connect(db)
    if os.environ.get("SKIP_SCAN") != "1":
        for rel in SHELF_ROOTS + WORK_ROOTS:
            root = EBOOKS / rel
            if root.is_dir():
                print(scan_root(con, root), flush=True)
    rep = build_report(con, get_dirs()["data"])
    print("verdicts:", rep["counts"])
    print("GB:", rep["GB"])
    print("works on shelves:", rep["shelf_works"])
    for (root, verdict), n in sorted(rep["by_root"].items()):
        print(f"  {root:28} {verdict:24} {n}")
    print("report:", get_dirs()["data"] / "robot_report.tsv")


if __name__ == "__main__":
    main()
