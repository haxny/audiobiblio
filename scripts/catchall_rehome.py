"""Move the catch-all program's history to the real programs.

Run inside the container:  python scripts/catchall_rehome.py [write]
Dry run by default; prints counts and the unresolved shows.
See audiobiblio.library.pipelines.catchall_rehome.
"""
from __future__ import annotations

import sys

from audiobiblio.core.db.models import Program
from audiobiblio.core.db.session import get_session
from audiobiblio.library.pipelines.catchall_rehome import rehome
from audiobiblio.library.pipelines.program_resolve import is_catch_all


def main() -> None:
    write = sys.argv[1:] == ["write"]
    s = get_session()
    catch_all = next(p for p in s.query(Program).all() if is_catch_all(p))
    stats, per_program, unresolved = rehome(s, catch_all, write)
    print("WRITE" if write else "DRY RUN", dict(stats))
    print("-- to programs:")
    for k, v in per_program.most_common(40):
        print(f"{v:>6}  {k}")
    print(f"-- unresolved shows ({len(unresolved)}):")
    for k, v in unresolved.most_common(40):
        print(f"{v:>6}  {k}")


if __name__ == "__main__":
    main()
