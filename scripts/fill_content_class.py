"""Fill crawl_targets.content_class from the 2026-11-07 classification.

Input: the classification JSON ({"summary": ..., "targets": {CLASS: [{id, ...}]}}).
Dry-run by default; --apply writes. Idempotent: only NULL classes are
filled (a different existing class is reported as a conflict and kept,
unless --overwrite). --guess-unlisted classifies AUTO targets missing from
the JSON by name keywords (books / dramas / music only — anything unsure
stays NULL and is listed for the owner).

Usage (production, inside the container):
    docker cp review_targets_classified.json audiobiblio:/tmp/classified.json
    docker exec -i audiobiblio python scripts/fill_content_class.py \
        --json /tmp/classified.json [--guess-unlisted] [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.acquire.priority import BOOK, CONTENT_CLASSES, DRAMA, MUSIC_OTHER
from audiobiblio.core.db.models import ApprovalMode, CrawlTarget

# Whole-word keyword stems on the unaccented, lowercased target name.
_GUESS_RULES = (
    (DRAMA, r"\b(hra|hry|drama|dramatapas)\b"),
    (BOOK, r"\b(cetb\w*|cteni\w*|povid\w*|radiokni\w*|audiokni\w*|pohad\w*|"
           r"poctenicko|poezie|basn\w*|literatur\w*|hajaja|vypraveci|zilibyli)\b"),
    (MUSIC_OTHER, r"\b(pisnick\w*|country|folk\w*|rock\w*|hudba|musica|opera|"
                  r"notovani|slavici)\b"),
)


# Owner decisions 2026-11-07 for targets missing from the JSON (they were
# AUTO before the classification): rank 1, books.
OWNER_OVERRIDES: dict[int, str] = {
    69: BOOK,  # Toulky českou minulostí
    51: BOOK,  # Osudy
}


@dataclass
class Report:
    planned: dict[int, str] = field(default_factory=dict)       # id → new class
    conflicts: dict[int, tuple[str, str]] = field(default_factory=dict)
    missing_ids: list[int] = field(default_factory=list)        # in JSON, not in DB
    unchanged: int = 0
    per_class: dict[str, int] = field(default_factory=dict)     # final state, all targets
    unclassified_auto: list[tuple[int, str]] = field(default_factory=list)


def load_mapping(path: Path) -> dict[int, str]:
    data = json.loads(Path(path).read_text())
    out: dict[int, str] = {}
    for cls, rows in (data.get("targets") or {}).items():
        if cls not in CONTENT_CLASSES:
            raise ValueError(f"unknown class {cls!r} (expected one of {CONTENT_CLASSES})")
        for row in rows:
            out[int(row["id"])] = cls
    return out


def guess_class(name: str | None) -> str | None:
    folded = unidecode(name or "").lower()
    for cls, pattern in _GUESS_RULES:
        if re.search(pattern, folded):
            return cls
    return None


def _wanted(t: CrawlTarget, mapping: dict[int, str], guess_unlisted: bool) -> str | None:
    if t.id in mapping:
        return mapping[t.id]
    if guess_unlisted and t.approval_mode == ApprovalMode.AUTO:
        return guess_class(t.name)
    return None


def fill(s: Session, mapping: dict[int, str], apply: bool = False,
         overwrite: bool = False, guess_unlisted: bool = False) -> Report:
    report = Report()
    targets = {t.id: t for t in s.query(CrawlTarget).all()}
    report.missing_ids = sorted(set(mapping) - set(targets))
    mapping = {**mapping, **OWNER_OVERRIDES}
    final: dict[int, str | None] = {}
    for tid, t in sorted(targets.items()):
        want = _wanted(t, mapping, guess_unlisted)
        final[tid] = t.content_class
        if want is None or want == t.content_class:
            report.unchanged += int(want is not None)
            continue
        if t.content_class is not None and not overwrite:
            report.conflicts[tid] = (t.content_class, want)
            continue
        report.planned[tid] = want
        final[tid] = want
        if apply:
            t.content_class = want
    if apply and report.planned:
        s.commit()
    report.per_class = dict(Counter(c for c in final.values() if c))
    report.unclassified_auto = [
        (tid, t.name) for tid, t in sorted(targets.items())
        if final[tid] is None and t.approval_mode == ApprovalMode.AUTO and t.active]
    return report


def _print(report: Report, apply: bool) -> None:
    mode = "APPLIED" if apply else "DRY-RUN (nothing written; --apply to write)"
    print(f"== fill_content_class: {mode}")
    print(f"changes: {len(report.planned)}  unchanged: {report.unchanged}  "
          f"conflicts: {len(report.conflicts)}  missing ids: {len(report.missing_ids)}")
    for cls, n in sorted(Counter(report.planned.values()).items()):
        print(f"  set {cls:<12} {n}")
    print("final per class:", dict(sorted(report.per_class.items())))
    for tid, (old, new) in sorted(report.conflicts.items()):
        print(f"  conflict id={tid}: {old} (kept) vs {new}")
    if report.missing_ids:
        print("missing ids:", report.missing_ids[:50])
    print(f"active AUTO targets still unclassified: {len(report.unclassified_auto)}")
    for tid, name in report.unclassified_auto:
        print(f"  {tid}  {name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace an existing different class")
    ap.add_argument("--guess-unlisted", action="store_true",
                    help="keyword-classify AUTO targets missing from the JSON")
    ap.add_argument("--db", default=None, help="SQLAlchemy URL (default: app DB)")
    args = ap.parse_args(argv)
    from audiobiblio.core.db.session import get_session
    s = get_session(args.db)
    report = fill(s, load_mapping(args.json), apply=args.apply,
                  overwrite=args.overwrite, guess_unlisted=args.guess_unlisted)
    _print(report, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
