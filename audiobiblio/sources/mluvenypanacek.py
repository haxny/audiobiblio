"""mluvenypanacek.cz — Czech spoken-word catalog (WordPress REST, ~130k posts).

The identification database: credits, cast with roles, recording dates,
premiere/reprises, CD release with length, numbered parts. Rule (user,
2026-10-09): checked for changes at least monthly; our data is analysed
against it to enrich ID3 tags and find missing episodes and works.

sync(): the first run dumps everything (resumable), later runs fetch only
posts modified since the previous sync (WP `modified_after`). Raw JSON lines
— parsing works on the dump and never re-hits the site.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import structlog

log = structlog.get_logger()

API = "https://mluvenypanacek.cz/wp-json/wp/v2/posts"
GAP_S = 3.0
PER_PAGE = 100
FIELDS = "id,slug,link,date,modified,categories,tags,title,content,excerpt"
UA = {"User-Agent": "audiobiblio/1.0 (private audiobook library catalog; polite crawl)"}
STATE = "sync_state.json"


def fetch(page: int, modified_after: str | None) -> tuple[list[dict], int]:
    q = {"per_page": PER_PAGE, "page": page, "_fields": FIELDS, "orderby": "id", "order": "asc"}
    if modified_after:
        q["modified_after"] = modified_after
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(q)}", headers=UA)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()), int(r.headers.get("X-WP-TotalPages", "0"))
        except urllib.error.HTTPError as e:
            if e.code == 400:            # page beyond the end
                return [], 0
            time.sleep(30 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"page {page} failed 5×")


def _dump(out: Path, modified_after: str | None) -> int:
    cursor = out.with_name(out.name + ".page")
    page = int(cursor.read_text()) + 1 if cursor.exists() else 1
    n = 0
    with out.open("a") as f:
        while True:
            posts, total = fetch(page, modified_after)
            if not posts:
                break
            for p in posts:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
            n += len(posts)
            f.flush()
            cursor.write_text(str(page))
            if page % 20 == 0:
                log.info("panacek_sync_progress", page=page, total=total)
            page += 1
            time.sleep(GAP_S)
    return n


def sync(out_dir: Path, now: str) -> dict:
    """Full dump if none is complete yet, else changes since the last sync."""
    out_dir.mkdir(parents=True, exist_ok=True)
    state_file = out_dir / STATE
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    since = state.get("last_sync")
    name = f"changes-{now[:10]}.jsonl" if since else "posts.jsonl"
    n = _dump(out_dir / name, since)
    state_file.write_text(json.dumps({"last_sync": now, "last_file": name}))
    log.info("panacek_sync_done", file=name, posts=n, since=since)
    return {"file": name, "posts": n, "modified_after": since}
