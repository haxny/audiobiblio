"""Inventory of unprocessed audiobook backups (read-only).

    python scripts/backup_inventory.py /media/download/"povidky a hry 05 R-S" [...]

For every book folder (a directory holding audio files): integrity of each
file (ffprobe + decode of the last seconds catches truncated files), parsed
name and !Info .txt, best mluvenypanacek.cz match, and the closest folder on
the curated shelves (fiction / nonfiction) with a verdict:

  IDENTICAL   same audio file count and sizes as the shelf folder
  SAME-LENGTH same total length within 2 % (likely the same recording)
  OTHER       shelf has the title, but a different recording
  NEW         title not on the shelf
  BROKEN      at least one audio file does not decode

Writes one JSON line per folder to /app/data/backup_inventory.jsonl and
prints a summary. Nothing is moved or deleted here.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from unidecode import unidecode

from audiobiblio.sources.panacek_index import find as panacek_find

AUDIO = {".mp3", ".m4a", ".m4b", ".ogg", ".opus", ".flac", ".wav", ".wma", ".aac"}
SHELVES = (Path("/media/fiction"), Path("/media/nonfiction"))
PANACEK_DB = "/app/data/audiobiblio/panacek.sqlite3"
OUT = Path("/app/data/backup_inventory.jsonl")

NAME_RE = re.compile(r"^(?P<author>[^-]+?)\s+-\s+(?P<rest>.+)$")
LEN_RE = re.compile(r"\((?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\)\s*$")
YEAR_RE = re.compile(r"((?:19|20)\d\d)")


def norm(s: str | None) -> str:
    s = unidecode(s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    return " ".join(re.findall(r"[a-z0-9]+", s))


def parse_name(name: str) -> dict:
    """'Rais Karel Václav - Pro číslo (Přemysl Rut)2011(27m)'."""
    out: dict = {"raw": name}
    m = LEN_RE.search(name)
    if m and any(m.groups()):
        h, mi, se = (int(x) if x else 0 for x in m.groups())
        out["minutes"] = h * 60 + mi + se / 60
        name = name[:m.start()]
    m = NAME_RE.match(name)
    if m:
        out["author"] = m["author"].strip()
        rest = m["rest"]
    else:
        rest = name
    y = YEAR_RE.findall(rest)
    if y:
        out["year"] = int(y[-1])
    narr = re.findall(r"\(([^)]*)\)", rest)
    if narr:
        out["narrator"] = narr[-1].strip()
    out["title"] = re.split(r"\s*\(|\s+ČRo|\s+CRo|\s+(?:19|20)\d\d", rest)[0].strip()
    return out


def read_info(folder: Path) -> dict:
    """!Info / Chaves-style .txt (cp1250) → Název, Autor, Cyklus, rok, žánr."""
    keys = {"Název": "title", "Autor": "author", "Cyklus": "cycle",
            "Natočeno v roce": "year", "Žánr": "genre", "Účinkují": "cast",
            "Čte": "narrator", "Celkový čas": "length"}
    for txt in sorted(folder.glob("*.txt")):
        raw = txt.read_bytes()
        for enc in ("utf-8", "cp1250"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        info = {}
        for line in text.splitlines():
            k, _, v = line.partition(":")
            k = k.strip()
            if k in keys and v.strip():
                info[keys[k]] = v.strip()
        if info:
            info["file"] = txt.name
            return info
    return {}


def probe(f: Path) -> dict:
    try:
        d = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
            capture_output=True, text=True, timeout=60).stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "no duration"}
    try:
        tail = subprocess.run(
            ["ffmpeg", "-v", "error", "-sseof", "-5", "-i", str(f), "-f", "null", "-"],
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "seconds": round(d, 1), "error": "tail decode timeout"}
    err = tail.stderr.strip().splitlines()
    return {"ok": not err, "seconds": round(d, 1), "error": err[0][:200] if err else None}


def book_folders(root: Path):
    for d in sorted(p for p in root.rglob("*") if p.is_dir() and "@eaDir" not in p.parts):
        if any(f.suffix.lower() in AUDIO for f in d.iterdir() if f.is_file()):
            yield d
    if any(f.suffix.lower() in AUDIO for f in root.iterdir() if f.is_file()):
        yield root


def shelf_index() -> dict[str, list[Path]]:
    """title key -> shelf folders (author dir / book dir), any naming style."""
    idx: dict[str, list[Path]] = {}
    for shelf in SHELVES:
        for adir in shelf.iterdir():
            if not adir.is_dir() or adir.name.startswith(("@", "#")):
                continue
            for bdir in adir.iterdir():
                if not bdir.is_dir() or bdir.name.startswith("@"):
                    continue
                p = parse_name(bdir.name)
                idx.setdefault(norm(p.get("title") or bdir.name), []).append(bdir)
    return idx


def audio_files(d: Path) -> list[Path]:
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO)


def total_seconds(files: list[Path]) -> float:
    return sum(probe(f).get("seconds") or 0 for f in files)


def compare(folder: Path, files: list[Path], secs: float, author: str | None,
            cands: list[Path]) -> tuple[str, str | None]:
    a_tok = set(norm(author).split())
    best: tuple[str, str | None] = ("NEW", None)
    for c in cands:
        if a_tok and not a_tok & set(norm(c.parent.name + " " + c.name).split()):
            continue                       # same title, other author
        theirs = audio_files(c)
        if [f.stat().st_size for f in files] == [f.stat().st_size for f in theirs]:
            return "IDENTICAL", str(c)
        tsecs = total_seconds(theirs)
        if secs and tsecs and abs(secs - tsecs) / max(secs, tsecs) <= 0.02:
            best = ("SAME-LENGTH", str(c))
        elif best[0] == "NEW":
            best = ("OTHER", str(c))
    return best


def done_folders() -> set[str]:
    """Folders already in the output — a rerun continues where it stopped."""
    if not OUT.exists():
        return set()
    return {json.loads(line)["folder"] for line in OUT.open() if line.strip()}


def author_hint(root: Path) -> str | None:
    """Per-author share dirs ('Sdílení/Jo Nesbo'): the root names the author."""
    name = re.sub(r"\s+-\S.*$", "", root.name).strip()   # 'Dmitry Glukhovsky -Metro'
    return None if re.match(r"(?i)povidky|lovci", name) else name


def main(roots: list[str]) -> None:
    shelf = shelf_index()
    seen = done_folders()
    con = sqlite3.connect(PANACEK_DB)
    verdicts: Counter = Counter()
    sizes: Counter = Counter()
    with OUT.open("a") as out:
        for root in roots:
            hint = author_hint(Path(root))
            for folder in book_folders(Path(root)):
                if str(folder) in seen:
                    continue
                try:
                    rec = inspect(folder, hint, shelf, con)
                except Exception as exc:  # one bad folder must not stop the run
                    rec = {"folder": str(folder), "verdict": "ERROR", "error": repr(exc)[:300],
                           "bytes": 0}
                verdicts[rec["verdict"]] += 1
                sizes[rec["verdict"]] += rec["bytes"]
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
    for v, n in verdicts.most_common():
        print(f"{v:12} {n:5} folders  {sizes[v] / 1e9:6.1f} GB")


def clean_title(t: str) -> str:
    """'4.0 - Carodej a sklo', '1 Pan Mercedes', 'To  Audiokniha' -> bare title."""
    t = re.sub(r"(?i)\baudiokniha\b", " ", t)
    t = re.sub(r"^[\d.\s-]+(?=\D)", "", t)
    return " ".join(t.split()) or t


def inspect(folder: Path, hint: str | None, shelf: dict, con) -> dict:
    files = audio_files(folder)
    probes = {f.name: probe(f) for f in files}
    secs = sum(p.get("seconds") or 0 for p in probes.values())
    name = parse_name(folder.name)
    info = read_info(folder)
    title = info.get("title") or name.get("title") or folder.name
    author = name.get("author") or info.get("author")
    if hint and not set(norm(hint).split()) & set(norm(author).split()):
        # 'Sdílení/Jo Nesbo/Harry Hole 01 - Netopýr': the prefix is a series,
        # not the author; the share dir names the author.
        author = hint
        title = info.get("title") or folder.name.split(" - ", 1)[-1]
    title = clean_title(title)
    pan = panacek_find(con, title, author=author, narrator=name.get("narrator"),
                       minutes=secs / 60 or None, limit=1)
    if not all(p["ok"] for p in probes.values()):
        verdict, match = "BROKEN", None
    else:
        verdict, match = compare(folder, files, secs, author, shelf.get(norm(title), []))
    return {
        "folder": str(folder), "verdict": verdict, "shelf": match,
        "files": len(files), "bytes": sum(f.stat().st_size for f in files),
        "minutes": round(secs / 60, 1), "name": name, "info": info, "author": author,
        "title": title,
        "broken": {k: v["error"] for k, v in probes.items() if not v["ok"]},
        "panacek": ({k: pan[0].get(k) for k in ("id", "title_base", "authors",
                     "narrators", "total_minutes", "score")} if pan else None),
    }


if __name__ == "__main__":
    main(sys.argv[1:])
