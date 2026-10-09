"""Manual run of the mluvenypanacek.cz sync (the scheduler runs it monthly).

  python scripts/panacek_dump.py     # full dump first time, then changes only
"""
from datetime import datetime, timezone

from audiobiblio.paths import get_dirs
from audiobiblio.sources.mluvenypanacek import sync

if __name__ == "__main__":
    print(sync(get_dirs()["data"] / "panacek",
               now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")))
