"""Normalize narrator names across Audiobookshelf libraries (2026-10-09).

ABS shows the same person under many spellings ("Jiri Schwarz", "Jiří
Schwarz", mojibake "Jiøí Schwarz", "(Jiří Schwarz)2001(") because narrators
come from tags/folder names of decades of hand-made files. Canonical form
(user rule): unidecoded name list, mojibake repaired, roles/years/junk
stripped — the same clean_person_names() audiobiblio uses.

Per library: GET /api/libraries/:id/narrators, then for each narrator whose
canonical form differs:
  - canonical exists            → PATCH rename (ABS merges into it)
  - canonical is a new string   → PATCH rename
  - no name left (junk)         → listed only ("delete" needs GO_DELETE=1)
Needs ABS >= 2.36.1 (older versions renamed across ALL libraries).

Dry run writes a TSV for review; GO=1 applies renames.
Usage (container): python scripts/abs_narrators.py [GO=1] [GO_DELETE=1]
"""
import base64
import csv
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from unidecode import unidecode

from audiobiblio.library.book_meta import clean_person_names

U = os.environ["ABS_URL"].rstrip("/")
K = os.environ["ABS_API_KEY"]
GO = os.environ.get("GO") == "1"
GO_DELETE = os.environ.get("GO_DELETE") == "1"
OUT = Path(os.environ.get("OUT", "/app/data/audiobiblio/abs_narrators_proposal.tsv"))


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        U + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + K, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return r.status, json.loads(txt) if txt.strip()[:1] in "[{" else txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def demojibake(s: str) -> str:
    """Czech text decoded as latin-1/cp1252 ("Jiøí", "Bare\\x9a") → cp1250."""
    for enc in ("latin-1", "cp1252"):
        try:
            fixed = s.encode(enc).decode("cp1250")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != s and re.search(r"[ěščřžýáíéůúňťďĚŠČŘŽÝÁÍÉŮÚŇŤĎ]", fixed):
            return fixed
    return s


_WRAPPED_RE = re.compile(r"^\s*\(([^()]{3,80})\)")          # "(Jiří Schwarz)2001("
_ROLE_RE = re.compile(r"^[^()]+\(([^()]+)\)\s*$")               # "Josef Schwarz (Marek Lambora)"
_TOKEN = re.compile(r"^[A-Z][a-z'.-]+$")


def _split_glued(name: str) -> str:
    """'Jiri Schwarz Jan Novotny' → 'Jiri Schwarz, Jan Novotny' (exactly 2+2)."""
    t = name.split()
    if len(t) == 4 and "," not in name and all(_TOKEN.match(x) for x in t):
        return f"{t[0]} {t[1]}, {t[2]} {t[3]}"
    return name


def _one(part: str) -> str | None:
    part = unidecode(demojibake(part)).strip()
    m = _WRAPPED_RE.match(part)
    if m:
        part = m.group(1)
    return clean_person_names(_split_glued(part))


def canonical(name: str) -> str | None:
    """Canonical narrator string, None for junk. Ambiguous 'Character
    (Actor)' values are returned UNCHANGED — never guess which one reads."""
    m = _ROLE_RE.match(name)
    if m and clean_person_names(unidecode(m.group(1))):
        return name
    parts = [p for p in re.split(r"\x00+", name) if p.strip()]
    cleaned = [_one(p) for p in parts]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return None
    return cleaned[0] if len(cleaned) == 1 else ", ".join(cleaned)


def main() -> None:
    _, libs = call("GET", "/api/libraries")
    rows = []
    for lib in libs["libraries"]:
        if lib.get("mediaType") != "book":
            continue
        st, data = call("GET", f"/api/libraries/{lib['id']}/narrators")
        if st != 200:
            print(f"{lib['name']}: narrators HTTP {st}")
            continue
        existing = {n["name"] for n in data.get("narrators", [])}
        for n in data.get("narrators", []):
            new = canonical(n["name"])
            if new == n["name"]:
                continue
            action = "delete" if new is None else ("merge" if new in existing else "rename")
            rows.append([lib["name"], action, n["name"], new or "", n.get("numBooks", "")])
            if (GO and new) or (GO_DELETE and new is None):
                nid = n.get("id") or base64.b64encode(n["name"].encode()).decode()
                st2, _ = (call("PATCH", f"/api/libraries/{lib['id']}/narrators/{nid}", {"name": new})
                          if new else call("DELETE", f"/api/libraries/{lib['id']}/narrators/{nid}"))
                rows[-1].append(f"HTTP {st2}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["library", "action", "old", "new", "books", "result"])
        w.writerows(rows)
    from collections import Counter
    print("changes by action:", dict(Counter(r[1] for r in rows)))
    print("books affected:", sum(int(r[4] or 0) for r in rows))
    print("proposal:", OUT, "(applied)" if GO else "(dry run)")


if __name__ == "__main__":
    main()
