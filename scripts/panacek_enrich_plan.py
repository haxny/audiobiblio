"""Dry run: match audiobiblio works with audio against the mluvenypanacek
index and report what would change (fill / replace / conflict). No writes.

  python scripts/panacek_enrich_plan.py   → data/panacek_enrich_plan.tsv
"""
import csv
import sqlite3
from collections import Counter

from sqlalchemy import exists

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, Work
from audiobiblio.core.db.session import get_session
from audiobiblio.library.panacek_enrich import plan_work
from audiobiblio.paths import get_dirs

s = get_session()
con = sqlite3.connect(get_dirs()["data"] / "panacek.sqlite3")
works = (s.query(Work).filter(exists().where((Episode.work_id == Work.id) & exists().where(
    (Asset.episode_id == Episode.id) & (Asset.type == AssetType.AUDIO) & (Asset.status == AssetStatus.COMPLETE)))).all())
verdicts, actions, rows = Counter(), Counter(), []
for i, w in enumerate(works):
    p = plan_work(s, con, w)
    verdicts[p["verdict"]] += 1
    for field, d in p["fields"].items():
        actions[(field, d["action"])] += 1
        rows.append([w.id, f"http://nasx:8321/works/{w.id}", p["title"], field, d["current"], d["proposed"], d["action"], p["link"]])
    if i % 2000 == 0:
        print("…", i, flush=True)
out = get_dirs()["data"] / "panacek_enrich_plan.tsv"
with out.open("w", newline="") as f:
    csv.writer(f, delimiter="\t").writerows([["work_id", "gui", "title", "field", "current", "proposed", "action", "source"]] + rows)
print("works with audio:", len(works), "| verdicts:", dict(verdicts))
for (field, action), n in sorted(actions.items()):
    print(f"  {field:14} {action:9} {n}")
print("plan:", out)
