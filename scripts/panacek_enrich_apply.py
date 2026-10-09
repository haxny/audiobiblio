"""Apply mluvenypanacek enrichment: fill + replace only (conflicts are the
user's). Re-plans every work on fresh data, commits in batches, then refreshes
metadata.json of affected shelved books. Log: data/panacek_enrich_applied.tsv
"""
import csv
import sqlite3
from collections import Counter
from pathlib import Path

from sqlalchemy import exists

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, MetadataValue, Work
from audiobiblio.core.db.session import get_session
from audiobiblio.core.provenance import resolve_field
from audiobiblio.library.abs_metadata import build_abs_metadata, mark_library_dirty, write_abs_metadata
from audiobiblio.library.panacek_enrich import apply_plan, plan_work
from audiobiblio.paths import get_dirs

s = get_session()
con = sqlite3.connect(get_dirs()["data"] / "panacek.sqlite3")
works = (s.query(Work).filter(exists().where((Episode.work_id == Work.id) & exists().where(
    (Asset.episode_id == Episode.id) & (Asset.type == AssetType.AUDIO) & (Asset.status == AssetStatus.COMPLETE)))).all())
counts, log_rows, touched = Counter(), [], []
for i, w in enumerate(works):
    plan = plan_work(s, con, w)
    if plan["verdict"] != "matched":
        continue
    written = apply_plan(s, w, plan)
    if written:
        touched.append(w)
        for f in written:
            counts[f] += 1
            d = plan["fields"][f]
            log_rows.append([w.id, f"http://nasx:8321/works/{w.id}", plan["title"], f, d["current"], d["proposed"], d["action"], plan["link"]])
    if i % 200 == 0:
        s.commit()
s.commit()
out = get_dirs()["data"] / "panacek_enrich_applied.tsv"
with out.open("w", newline="") as f:
    csv.writer(f, delimiter="\t").writerows([["work_id", "gui", "title", "field", "before", "after", "action", "source"]] + log_rows)
md = 0
for w in touched:
    fp = resolve_field(s.query(MetadataValue).filter_by(entity_type="work", entity_id=w.id, field="final_path").all())
    if fp and Path(fp.value).is_dir():
        md += write_abs_metadata(Path(fp.value), build_abs_metadata(s, w))
        mark_library_dirty(Path(fp.value))
print("works changed:", len(touched), "| fields:", dict(counts), "| shelved metadata.json refreshed:", md)
print("log:", out)
