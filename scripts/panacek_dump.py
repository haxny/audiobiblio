"""Raw dump of mluvenypanacek.cz via its WordPress REST API (2026-10-09).

mluvenypanacek.cz catalogs Czech spoken word (130k records: radio documents,
plays, readings, serials…). Each post is free text with a steady pattern
(credits, cast with roles, recording dates, premiere/reprises, CD release
with length, numbered parts) — the identification database for telling
versions apart and checking completeness by length.

Step 1 only: store every post verbatim as JSON lines, resumable, polite
(one request per GAP_S). Parsing works on the dump, never re-hitting the site.

  python scripts/panacek_dump.py                 # full dump / resume
  MODIFIED_AFTER=2026-10-01T00:00:00 python …    # incremental (changes only)
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://mluvenypanacek.cz/wp-json/wp/v2/posts"
OUT_DIR = Path(os.environ.get("OUT_DIR", "/app/data/audiobiblio/panacek"))
GAP_S = float(os.environ.get("GAP_S", "3"))
PER_PAGE = 100
FIELDS = "id,slug,link,date,modified,categories,tags,title,content,excerpt"
UA = {"User-Agent": "audiobiblio/1.0 (private audiobook library catalog; polite crawl)"}


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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modified_after = os.environ.get("MODIFIED_AFTER")
    name = "posts.jsonl" if not modified_after else f"changes-{modified_after[:10]}.jsonl"
    out, state = OUT_DIR / name, OUT_DIR / (name + ".page")
    page = int(state.read_text()) + 1 if state.exists() else 1
    total = None
    with out.open("a") as f:
        while True:
            posts, pages = fetch(page, modified_after)
            total = pages or total
            if not posts:
                break
            for p in posts:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
            f.flush()
            state.write_text(str(page))
            if page % 20 == 0:
                print(f"page {page}/{total} ({page * PER_PAGE} posts)", flush=True)
            page += 1
            time.sleep(GAP_S)
    print(f"done: {out} (last page {page - 1}/{total})", flush=True)


if __name__ == "__main__":
    main()
