"""
Apply the approved genre clean-up table to audio files and Audiobookshelf.

Run inside the audiobiblio container (ABS /audiobooks == container /media/ebooks):
    python scripts/genre_apply.py --map genre_map.json --items abs_items.json --dry-run
    python scripts/genre_apply.py --map genre_map.json --items abs_items.json --limit 20
    python scripts/genre_apply.py --undo journal.jsonl

Only genres listed in the map change; everything else in a file is untouched.
Every file write is journaled (old -> new) so a run can be undone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from audiobiblio.library.abs import AbsClient
from audiobiblio.tags.genre_canon import apply_genre_map, load_genre_map, split_genres
from audiobiblio.tags.writer import GenreWriteError, read_genre, write_genre

ABS_ROOT = "/audiobooks"
LOCAL_ROOT = "/media/ebooks"
AUDIO_EXT = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus")
LIBRARIES = {"Fiction", "Kids", "Nonfiction [audio]", "Languages", "Medical [audio]", "Zlín"}


def local_path(abs_path: str) -> Path:
    return Path(LOCAL_ROOT) / abs_path[len(ABS_ROOT):].lstrip("/")


def audio_files(item_path: Path) -> list[Path]:
    if item_path.is_file():
        return [item_path] if item_path.suffix.lower() in AUDIO_EXT else []
    return sorted(p for p in item_path.rglob("*")
                  if p.suffix.lower() in AUDIO_EXT and not any(s.startswith("_") for s in p.parts))


def plan(items: list[dict], mapping: dict) -> list[dict]:
    """Items whose ABS genres change under the map."""
    out = []
    for it in items:
        if it.get("lib") not in LIBRARIES or not it.get("audio"):
            continue
        old = split_genres(it.get("genres") or [])
        new = apply_genre_map(old, mapping)
        if new != old:
            out.append({**it, "old": old, "new": new})
    return out


def apply_item(item: dict, mapping: dict, abs_client: AbsClient | None, journal) -> dict:
    """Rewrite genres of one item's files, then update ABS. Returns counters."""
    stats = {"files": 0, "written": 0, "errors": 0}
    for f in audio_files(local_path(item["path"])):
        stats["files"] += 1
        try:
            old = read_genre(f)
            new = apply_genre_map(old, mapping)
            if new == old:
                continue
            write_genre(f, new)
            journal.write(json.dumps({"path": str(f), "old": old, "new": new}, ensure_ascii=False) + "\n")
            stats["written"] += 1
        except (GenreWriteError, Exception) as e:  # one bad file must not stop the run
            stats["errors"] += 1
            print(f"  ERROR {f}: {e}", file=sys.stderr)
    if abs_client is not None and stats["errors"] == 0:
        stats["abs"] = patch_abs(abs_client, item)
    return stats


def patch_abs(abs_client: AbsClient, item: dict) -> bool:
    """Set the item's genres in ABS; a busy ABS gets one retry, then the file wins on next scan."""
    for attempt in (1, 2):
        try:
            abs_client.patch_item_media(item["id"], {"metadata": {"genres": item["new"]}}, timeout=60)
            return True
        except Exception as e:
            print(f"  ABS update failed (try {attempt}) {item['title']}: {e}", file=sys.stderr)
            time.sleep(5)
    return False


def undo(journal_path: Path) -> None:
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        rec = json.loads(line)
        write_genre(rec["path"], rec["old"])
    print(f"restored {len(lines)} files from {journal_path} (ABS: rescan the libraries)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map")
    ap.add_argument("--items")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--journal", default=f"/app/data/genre_journal_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    ap.add_argument("--undo")
    a = ap.parse_args()
    if a.undo:
        undo(Path(a.undo))
        return
    mapping = load_genre_map(a.map)
    todo = plan(json.loads(Path(a.items).read_text(encoding="utf-8")), mapping)
    print(f"{len(todo)} books change genre")
    if a.limit:
        todo = todo[: a.limit]
    if a.dry_run:
        for it in todo[:40]:
            print(f"  {it['lib']} | {it['title']}\n     {'; '.join(it['old'])}\n  -> {'; '.join(it['new'])}")
        return
    client = AbsClient.from_config() if os.environ.get("ABS_URL") else None
    total = {"files": 0, "written": 0, "errors": 0, "abs_failed": 0}
    with open(a.journal, "a", encoding="utf-8") as journal:
        for it in todo:
            s = apply_item(it, mapping, client, journal)
            s["abs_failed"] = int(client is not None and s.get("abs") is False)
            total = {k: total[k] + s.get(k, 0) for k in total}
            print(f"{it['lib']} | {it['title']} | {'; '.join(it['new'])} | files {s['written']}/{s['files']}"
                  + (f" ERRORS {s['errors']}" if s["errors"] else "")
                  + (" ABS-NOT-UPDATED" if s["abs_failed"] else ""))
    print(f"done: {total}; journal {a.journal}")


if __name__ == "__main__":
    main()
