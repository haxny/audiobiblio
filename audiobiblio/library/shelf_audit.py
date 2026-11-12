"""Audit of the curated shelves' metadata.json (what ABS shows).

Historic sources left three kinds of damage (user 2026-11-12, Otec Goriot):

  swapped        authors ↔ narrators: rozhlas mp3s carry the cast in `artist`
                 and the author in `composer`; the 2026-04 generator read them
                 the other way round
  diacritics     a title/name spelled with WRONG diacritics ("Řbitov zviřátek");
                 plain ASCII is a deliberate style and is never touched
  mojibake       UTF-8 read as cp1252 ("PÅ™Ã­bÄ›h")

Evidence, strongest first: the mluvenypanacek.cz record of the same title
with an overlapping person, the shelf's author folder ("Honore de Balzac
[audio]"), the text itself (mojibake). A finding backed by evidence is
`confident` and is applied by the nightly job; anything else is only
reported for the user. Every write is logged with the before/after values.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from unidecode import unidecode

from audiobiblio.library.abs_metadata import split_names
from audiobiblio.sources.panacek_index import find

_MOJIBAKE = re.compile(r"[ÃÄÅÂ][\u0080-ſ‘-›]")


@dataclass(frozen=True)
class Finding:
    folder: str
    verdict: str            # ok | swapped | wrong_author | diacritics | mojibake | roles_conflict | no_match
    confident: bool
    current: dict
    proposed: dict = field(default_factory=dict)
    reason: str = ""
    link: str | None = None


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", unidecode(s or "").lower())).strip()


_PLACEHOLDERS = {"ruzni", "ruzni autori", "ruzni interpreti", "mluvene slovo", "various",
                 "various artists", "neznamy", "neznamy autor", "unknown", "audiokniha",
                 "cesky rozhlas", "dalsi", "a dalsi"}


def _tokens(name: str) -> list[str]:
    return _norm(re.sub(r"\([^)]*\)", " ", name)).split()


def _surname_eq(a: str, b: str) -> bool:
    """'bronte' == 'bronteova' (Czech feminine form of a foreign surname)."""
    return a == b or a == b + "ova" or b == a + "ova"


def _first_names_fit(short: list[str], long: list[str]) -> bool:
    """Every given name / initial of one form fits one of the other ('a' → 'arthur')."""
    return all(any(t == u or (len(t) == 1 and u.startswith(t)) or (len(u) == 1 and t.startswith(u))
                   for u in long) for t in short)


def _same_person(a: str, b: str, strict: bool = False) -> bool:
    """'Balzac Honore de' == 'Honoré de Balzac'; 'A. C. Clarke' == 'Arthur Charles
    Clarke'; 'Emily Bronte (Ellis Bell)' == 'Emily Brontëová'; a two-token name
    inside a short longer one counts too ('Jan Vlasák ml.'), unless `strict`.
    A title glued to a name ('Clive Barker - První kniha krve') is no person."""
    if re.search(r"\s[-–]\s", a + " " + b) or "(" in a + b and ")" not in a + b:
        return False
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or max(len(ta), len(tb)) > 5:
        return False
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return True
    if not strict and min(len(sa), len(sb)) >= 2 and max(len(sa), len(sb)) <= 4 \
            and (sa <= sb or sb <= sa):
        return True
    if min(len(ta), len(tb)) < 2:
        return False
    for ta_ in (ta, ta[1:] + ta[:1]):           # 'Lindgren Astrid' = 'Astrid Lindgren…'
        if _surname_eq(ta_[-1], tb[-1]):
            short, long = sorted((ta_[:-1], tb[:-1]), key=len)
            if _first_names_fit(short, long):
                return True
    return False


def _clean_person(raw: str) -> list[str]:
    """Folder-name leftovers → people: '(Karel Höeger)1957(1h48m)' → ['Karel Höeger'];
    'Psano kurzivou (12.08.2016) - … - CRo Vltava' → [] (not a person)."""
    m = re.match(r"^\s*\(([^)]*)\)", raw)
    text = m[1] if m else raw
    out = []
    for name in split_names(text.strip(" ,&")):
        if re.search(r"\d|\s[-–]\s|[()]", name) or not 2 <= len(name.split()) <= 4:
            continue
        out.append(name)
    return out


def _people(names: list[str]) -> list[str]:
    return list(dict.fromkeys(p for n in names for p in _clean_person(n)))


def _any_same(name: str, others: list[str], strict: bool = False) -> str | None:
    return next((o for o in others if _same_person(name, o, strict)), None)


def fix_mojibake(s: str) -> str:
    """'PÅ™Ã­bÄ›h' → 'Příběh'; clean text comes back unchanged."""
    if not s or not _MOJIBAKE.search(s):
        return s
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not _MOJIBAKE.search(fixed):
            return fixed
    return s


def _names(values: list | None) -> list[str]:
    """Metadata name lists → single person names, placeholders dropped.
    'A/B' and 'A, B a C' split; 'Christie, Agatha' is one person."""
    out: list[str] = []
    for v in values or []:
        if not isinstance(v, str):
            continue
        for part in v.split("/"):
            m = re.fullmatch(r"\s*([^,]+?),\s*([^,\s]+)\s*", part)
            people = [f"{m[2]} {m[1]}"] if m and " a " not in part else split_names(part)
            out += [p for p in people if _norm(p) not in _PLACEHOLDERS]
    return out


def _author_dir(folder: Path) -> str:
    return re.sub(r"\s*\[audio\]\s*$", "", folder.parent.name, flags=re.I).strip()


def _catalog_record(con: sqlite3.Connection, title: str, people: list[str]) -> tuple[dict | None, str]:
    """Same title + at least one shared person. Two records of that title
    naming different authors → ambiguous (never guess)."""
    hits = [h for h in find(con, title, limit=20) if _norm(h["title_base"]) == _norm(title)]
    matched = []
    for h in hits:
        rec_people = json.loads(h["authors"]) + json.loads(h["narrators"]) \
            + [c["actor"] for c in json.loads(h["cast_json"])]
        if any(_any_same(p, rec_people) for p in people):
            matched.append(h)
    if not matched:
        return None, "none"
    authors = {frozenset(_norm(a) for a in json.loads(h["authors"])) for h in matched}
    if len(authors) > 1:
        return None, "ambiguous"
    return matched[0], "matched"


def _check_mojibake(current: dict) -> dict:
    proposed: dict = {}
    if current.get("title") and fix_mojibake(current["title"]) != current["title"]:
        proposed["title"] = fix_mojibake(current["title"])
    for key in ("authors", "narrators"):
        vals = current.get(key) or []
        fixed = [fix_mojibake(v) for v in vals]
        if fixed != vals:
            proposed[key] = fixed
    return proposed


def _wrong_spelling(ours: str, theirs: str) -> bool:
    """Same letters, but ours has WRONG accents or is SHOUTED. Plain ASCII is
    a deliberate style (file names, tags); case and punctuation of a normally
    written text are the editor's choice — neither counts."""
    if ours == theirs or _norm(ours) != _norm(theirs):
        return False
    if ours.isupper() and not theirs.isupper():
        return True
    def letters(x: str) -> str:
        return "".join(ch for ch in x.casefold() if ch.isalpha())
    return ours != unidecode(ours) and letters(ours) != letters(theirs)


def _respell(names: list[str], catalog: list[str]) -> list[str] | None:
    """Catalog spelling for wrongly spelled names; None when nothing changes."""
    out = []
    for n in names:
        form = _any_same(n, catalog)
        out.append(form if form and _wrong_spelling(n, form) else n)
    return out if out != names else None


def _swap(folder: Path, current: dict, authors: list[str], narrators: list[str],
          rec: dict, rec_authors: list[str], rec_perf: list[str]) -> Finding | None:
    """Catalog-backed role repair, or None when the roles look right."""
    a_is_author = any(_any_same(a, rec_authors) for a in authors)
    if not rec_authors or a_is_author:
        return None
    n_is_author = any(_any_same(n, rec_authors) for n in narrators)
    a_all_perf = bool(authors) and all(_any_same(a, rec_perf, strict=True) for a in authors)
    if not n_is_author and not a_all_perf:
        return None
    readers = [_catalog_form(a, rec_perf) for a in _people(authors)] \
        + [n for n in _people(narrators) if not _any_same(n, rec_authors)]
    reason = ("catalog: the metadata narrator is the author" if n_is_author
              else "catalog: the metadata author is the reader")
    return Finding(str(folder), "swapped", True, current,
                   {"authors": rec_authors, "narrators": list(dict.fromkeys(readers))},
                   reason, rec["link"])


def _catalog_form(name: str, rec_people: list[str]) -> str:
    return _any_same(name, rec_people) or name


def _check_catalog(folder: Path, current: dict, title: str, authors: list[str],
                   narrators: list[str], rec: dict) -> Finding:
    rec_authors = json.loads(rec["authors"])
    rec_perf = json.loads(rec["narrators"]) + [c["actor"] for c in json.loads(rec["cast_json"])]
    swap = _swap(folder, current, authors, narrators, rec, rec_authors, rec_perf)
    if swap is not None:
        return swap
    adir = _author_dir(folder)
    if rec_authors and not any(_any_same(a, rec_authors) for a in authors) \
            and any(_same_person(adir, ra) for ra in rec_authors):
        # catalog and the curated author folder agree: the metadata is junk,
        # a typo or a broken encoding ('Natočeno pro pořad…', 'Oldøich Danìk')
        proposed = {"authors": rec_authors}
        readers = [_catalog_form(p, rec_perf) for p in _people(authors)
                   if _any_same(p, rec_perf, strict=True)]
        if readers and not narrators:
            proposed["narrators"] = readers
        return Finding(str(folder), "wrong_author", True, current, proposed,
                       "catalog author = author folder", rec["link"])
    if rec_authors and authors and not any(_any_same(a, rec_authors) for a in authors):
        return Finding(str(folder), "roles_conflict", False, current,
                       {"authors": rec_authors}, "catalog names another author", rec["link"])
    proposed: dict = {}
    if _wrong_spelling(title, rec["title_base"]):
        proposed["title"] = rec["title_base"]
    for key, names, catalog in (("authors", authors, rec_authors),
                                ("narrators", narrators, rec_perf)):
        fixed = _respell(names, catalog) if current.get(key) else None
        if fixed:
            proposed[key] = fixed
    if proposed:
        return Finding(str(folder), "diacritics", True, current, proposed,
                       "catalog spelling", rec["link"])
    return Finding(str(folder), "ok", True, current, {}, "", rec["link"])


def audit_folder(folder: Path, meta: dict, con: sqlite3.Connection) -> Finding:
    folder = Path(folder)
    current = {k: meta.get(k) for k in ("title", "authors", "narrators")}
    proposed = _check_mojibake(current)
    if proposed:
        return Finding(str(folder), "mojibake", True, current, proposed, "UTF-8 read as cp1252")

    title = meta.get("title") or folder.name
    authors, narrators = _names(meta.get("authors")), _names(meta.get("narrators"))
    adir = _author_dir(folder)
    rec, status = _catalog_record(con, title, authors + narrators + [adir])
    if rec is not None:
        return _check_catalog(folder, current, title, authors, narrators, rec)

    # no catalog evidence: the curated author folder still knows the author
    if adir and any(_same_person(n, adir) for n in narrators) \
            and not any(_same_person(a, adir) for a in authors):
        new_authors = [n for n in narrators if _same_person(n, adir)]
        rest = [n for n in narrators if not _same_person(n, adir)]
        return Finding(str(folder), "swapped", True, current,
                       {"authors": new_authors, "narrators": _people(authors + rest)},
                       "author folder names the metadata narrator")
    reason = "catalog ambiguous" if status == "ambiguous" else "not in catalog"
    return Finding(str(folder), "no_match", False, current, {}, reason)


def _write_metadata(folder: Path, changes: dict) -> bool:
    """Merge `changes` into metadata.json — an empty list IS written (a
    placeholder narrator removed must not fall back to the file tags)."""
    from audiobiblio.core.fsperm import adopt_parent_owner
    path = folder / "metadata.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(current, dict):
        return False
    merged = {**current, **changes}
    if merged == current:
        return False
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    adopt_parent_owner(path)
    return True


def apply_finding(finding: Finding, log_path: Path) -> bool:
    """Write the proposal into metadata.json (merge) and log the change.
    Returns False when nothing changed (already applied)."""
    if not finding.proposed or not _write_metadata(Path(finding.folder), finding.proposed):
        return False
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "folder": finding.folder, "verdict": finding.verdict, "reason": finding.reason,
             "link": finding.link,
             "before": {k: finding.current.get(k) for k in finding.proposed},
             "after": finding.proposed}
    with Path(log_path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def undo_changes(log_path: Path, since: str | None) -> int:
    """Put back the `before` values of logged changes (newest first), all of
    them or those at/after `since` (ISO time). Returns how many were undone."""
    entries = [json.loads(line) for line in Path(log_path).read_text(encoding="utf-8").splitlines()
               if line.strip()]
    undone = 0
    for e in reversed(entries):
        if e.get("undone") or (since and e["at"] < since):
            continue
        before = {k: v for k, v in e["before"].items()}
        if _write_metadata(Path(e["folder"]), before):
            undone += 1
        e["undone"] = True
    Path(log_path).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                              encoding="utf-8")
    return undone


SHELVES = (Path("/media/fiction"), Path("/media/nonfiction"), Path("/media/ebooks/4kids"))
_SKIP_PARTS = {"@eaDir", "#recycle", "#snapshot"}


def _shelf_files(roots) -> list[Path]:
    return sorted(p for root in roots if Path(root).is_dir()
                  for p in Path(root).rglob("metadata.json")
                  if not _SKIP_PARTS & set(p.parts))


def run_shelf_audit(con: sqlite3.Connection, roots=SHELVES, *, apply: bool,
                    report_path: Path, log_path: Path, limit: int | None = None) -> dict:
    """Audit every metadata.json on the shelves. Writes the full report
    (one JSON line per finding that is not ok) and, with `apply`, writes the
    confident fixes. Returns counts per verdict + applied."""
    from collections import Counter

    from audiobiblio.library.abs_metadata import mark_library_dirty

    stats: Counter = Counter()
    with Path(report_path).open("w", encoding="utf-8") as report:
        for path in _shelf_files(roots)[:limit]:
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stats["unreadable"] += 1
                continue
            if not isinstance(meta, dict):
                stats["unreadable"] += 1
                continue
            f = audit_folder(path.parent, meta, con)
            stats[f.verdict] += 1
            if f.verdict == "ok":
                continue
            applied = apply and f.confident and apply_finding(f, log_path)
            if applied:
                stats["applied"] += 1
                mark_library_dirty(path.parent)
            report.write(json.dumps({**f.__dict__, "applied": bool(applied)},
                                    ensure_ascii=False) + "\n")
    return dict(stats)


def run_default(*, apply: bool, limit: int | None = None) -> dict:
    """Shelves + panacek index + report/log files in the data dir."""
    from audiobiblio.paths import get_dirs
    data = get_dirs()["data"]
    db = data / "panacek.sqlite3"
    if not db.exists():
        raise FileNotFoundError(f"mluvenypanacek index missing: {db}")
    con = sqlite3.connect(db)
    try:
        return run_shelf_audit(con, apply=apply, limit=limit,
                               report_path=data / "shelf_audit_report.jsonl",
                               log_path=data / "shelf_audit_changes.jsonl")
    finally:
        con.close()
