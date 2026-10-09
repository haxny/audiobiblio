"""One-off: clean non-MANUAL narrator observations (2026-10-09).

The article-enrich credits regex read "Čtenářský deník" as "Čte: nářský
deník" and "Vypráví o svém…" as a narrator; 1,252 episodes carried such
fragments, and file tags echoed them back as FILE observations. Every
non-MANUAL narrator value goes through clean_person_names():
  - None      → row deleted (fragment)
  - different → value replaced by the cleaned name list
MANUAL rows are never touched. Dry run by default; GO=1 writes and saves
a JSON backup of every touched row next to the DB.

Usage (in the container):  python scripts/clean_narrators.py   [GO=1]
"""
import json
import os
from collections import Counter
from datetime import datetime

from audiobiblio.core.db.models import FieldOrigin, MetadataValue
from audiobiblio.core.db.session import default_db_path, get_session
from audiobiblio.library.book_meta import clean_person_names

GO = os.environ.get("GO") == "1"


def main() -> None:
    s = get_session()
    rows = (s.query(MetadataValue)
            .filter(MetadataValue.field == "narrator",
                    MetadataValue.origin != FieldOrigin.MANUAL).all())
    deleted, changed, backup = Counter(), Counter(), []
    samples: dict[str, str | None] = {}
    for mv in rows:
        cleaned = clean_person_names(mv.value)
        if cleaned == mv.value:
            continue
        backup.append({"id": mv.id, "entity_type": mv.entity_type, "entity_id": mv.entity_id,
                       "origin": mv.origin.value, "source": mv.source, "value": mv.value})
        if len(samples) < 20:
            samples[mv.value[:70]] = cleaned
        if cleaned is None:
            deleted[mv.origin.value] += 1
            if GO:
                s.delete(mv)
        else:
            changed[mv.origin.value] += 1
            if GO:
                mv.value = cleaned
    print(f"narrator rows scanned: {len(rows)}")
    print(f"  delete (fragment): {sum(deleted.values())} {dict(deleted)}")
    print(f"  clean (changed):   {sum(changed.values())} {dict(changed)}")
    for before, after in samples.items():
        print(f"    {before!r} -> {after!r}")
    if GO:
        path = default_db_path().parent / f"narrator_cleanup_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(backup, ensure_ascii=False, indent=0))
        s.commit()
        print(f"written; backup of {len(backup)} rows: {path}")
    else:
        print("dry run — GO=1 to write")


if __name__ == "__main__":
    main()
