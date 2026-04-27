#!/usr/bin/env python3
"""
Standalone sktorrent.eu scraper — no project dependencies.
Only needs: requests, beautifulsoup4, sqlite3 (stdlib).

Usage:
    python3 sktorrent_scraper_standalone.py --scrape --all-categories \
        --user argappa --password torrentoveheslo

    python3 sktorrent_scraper_standalone.py --stats
    python3 sktorrent_scraper_standalone.py --probe --category 23
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sktorrent")

BASE_URL = "https://sktorrent.eu/torrent"

DB_PATH = os.environ.get(
    "SKTORRENT_DB",
    str(Path(__file__).resolve().parent / "sktorrent.db"),
)

CATEGORIES = {
    23: "knihy", 24: "mluvene_slovo",
    1: "filmy_cz", 5: "rozpravky", 14: "filmy_kamera", 15: "filmy_tit",
    20: "filmy_dvd", 31: "filmy_bez_tit",
    3: "filmy_3d", 19: "filmy_hd", 28: "bluray", 29: "bluray_3d", 43: "uhd",
    16: "serial", 17: "dokument", 42: "tv_porad", 44: "sport",
    2: "hudba", 22: "hudba_dj", 26: "hudebni_videa", 45: "soundtrack",
    18: "hry_win", 30: "hry_konzole", 37: "hry_linux", 59: "hry_mac",
    60: "hry_xxx", 63: "hry_vr",
    21: "programy", 27: "mobil",
    9: "xxx", 25: "ostatni",
}

DEFAULT_CATEGORIES = {23: "knihy", 24: "mluvene_slovo"}

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS torrent_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    info_hash TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    size_bytes INTEGER,
    size_display TEXT,
    seeders INTEGER DEFAULT 0,
    leechers INTEGER DEFAULT 0,
    uploaded_at TEXT,
    detail_url TEXT,
    torrent_url TEXT,
    genres TEXT,
    image_url TEXT,
    category_id INTEGER,
    status TEXT DEFAULT 'new',
    matched_path TEXT,
    scraped_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_te_category ON torrent_entries(category);
CREATE INDEX IF NOT EXISTS ix_te_status ON torrent_entries(status);
CREATE INDEX IF NOT EXISTS ix_te_hash ON torrent_entries(info_hash);
"""


def get_db_at(path=None):
    db = sqlite3.connect(path or DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(CREATE_TABLE)
    return db


def _parse_size(size_str):
    m = re.match(r"([\d.,]+)\s*(GB|MB|KB|TB)", size_str.strip(), re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1)
    # Handle European notation: "1.000.0" or "1.234,5" → normalize to float
    # If last separator is comma, it's decimal: "1.234,5" → "1234.5"
    # If multiple dots, all but last are thousands: "1.000.0" → "1000.0"
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1:
        parts = raw.rsplit(".", 1)
        raw = parts[0].replace(".", "") + "." + parts[1]
    try:
        val = float(raw)
    except ValueError:
        return None
    unit = m.group(2).upper()
    mult = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(val * mult.get(unit, 1))


def _parse_date(date_str):
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except ValueError:
            continue
    return None


def _extract_hash(url):
    m = re.search(r"[&?]id=([a-fA-F0-9]{40})", url)
    return m.group(1).lower() if m else None


class Scraper:
    def __init__(self, username, password, rate_limit=1.0):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        })
        self.username = username
        self.password = password
        self.rate_limit = rate_limit
        self._last_req = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_req
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_req = time.time()

    def _get(self, url):
        self._throttle()
        # Ensure URL is properly encoded (Unicode chars in path/query)
        from urllib.parse import quote, urlparse, urlunparse, parse_qs, urlencode
        parsed = urlparse(url)
        # Re-encode path and query with proper percent-encoding
        safe_path = quote(parsed.path, safe="/")
        safe_query = quote(parsed.query, safe="=&%+")
        safe_url = urlunparse(parsed._replace(path=safe_path, query=safe_query))
        r = self.session.get(safe_url, timeout=15)
        r.raise_for_status()
        return r

    def login(self):
        self._throttle()
        self.session.get(BASE_URL + "/", timeout=30)
        self._throttle()
        r = self.session.post(
            BASE_URL + "/login.php",
            data={"uid": self.username, "pwd": self.password},
            allow_redirects=True, timeout=30,
        )
        cookies = dict(self.session.cookies)
        ok = "uid" in cookies and "pass" in cookies
        log.info("Login: %s (cookies: %s)", "OK" if ok else "FAILED", list(cookies.keys()))
        return ok

    def probe(self, category_id=23):
        url = "%s/torrents_v2.php?active=0&order=data&by=DESC&category=%d&page=0" % (BASE_URL, category_id)
        log.info("Probing %s", url)
        return self._get(url).text

    def get_total_pages(self, html):
        nums = set()
        for m in re.finditer(r"page=(\d+)", html):
            nums.add(int(m.group(1)))
        return max(nums) if nums else 0

    def parse_page(self, html, cat_name, cat_id):
        soup = BeautifulSoup(html, "html.parser")
        entries = []
        for link in soup.find_all("a", href=re.compile(r"details\.php\?.*id=[a-fA-F0-9]{40}")):
            href = link.get("href", "")
            info_hash = _extract_hash(href)
            if not info_hash:
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                title_attr = link.get("title", "")
                title = re.sub(r"^Stiahni si .+? ", "", title_attr, count=1)
            if not title:
                continue

            cell = link.find_parent("td")
            if not cell:
                continue

            text = cell.get_text(" ", strip=True)

            size_display = ""
            size_bytes = None
            m = re.search(r"Velkost\s+([\d.,]+\s*(?:GB|MB|KB|TB))", text, re.IGNORECASE)
            if m:
                size_display = m.group(1)
                size_bytes = _parse_size(size_display)

            uploaded_at = None
            m = re.search(r"Pridany\s+(\d{2}/\d{2}/\d{4})", text)
            if m:
                uploaded_at = _parse_date(m.group(1))

            seeders = 0
            m = re.search(r"Odosielaju\s*:\s*(\d+)", text)
            if m:
                seeders = int(m.group(1))

            leechers = 0
            m = re.search(r"Stahuju\s*:\s*(\d+)", text)
            if m:
                leechers = int(m.group(1))

            genre_links = cell.find_all("a", href=re.compile(r"zaner="))
            genres = ", ".join(a.get_text(strip=True) for a in genre_links)

            img = link.find("img")
            image_url = ""
            if img:
                image_url = img.get("data-src", "") or img.get("src", "")

            detail_url = href if href.startswith("http") else BASE_URL + "/" + href

            entries.append({
                "info_hash": info_hash,
                "title": title,
                "category": cat_name,
                "category_id": cat_id,
                "size_bytes": size_bytes,
                "size_display": size_display,
                "seeders": seeders,
                "leechers": leechers,
                "uploaded_at": uploaded_at,
                "detail_url": detail_url,
                "torrent_url": "%s/download.php?id=%s" % (BASE_URL, info_hash),
                "genres": genres,
                "image_url": image_url,
            })
        return entries

    def scrape_category(self, cat_id, cat_name, max_pages=None):
        all_entries = []
        url = "%s/torrents_v2.php?active=0&order=data&by=DESC&category=%d&page=0" % (BASE_URL, cat_id)
        log.info("Fetching first page: %s", url)
        r = self._get(url)
        total_pages = self.get_total_pages(r.text)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        log.info("Category %s (id=%d): %d pages", cat_name, cat_id, total_pages + 1)

        entries = self.parse_page(r.text, cat_name, cat_id)
        all_entries.extend(entries)
        log.info("Page 0: %d entries", len(entries))

        empty_streak = 0
        for page in range(1, total_pages + 1):
            url = "%s/torrents_v2.php?active=0&order=data&by=DESC&category=%d&page=%d" % (BASE_URL, cat_id, page)
            try:
                r = self._get(url)
                entries = self.parse_page(r.text, cat_name, cat_id)
                all_entries.extend(entries)

                if not entries:
                    empty_streak += 1
                    if empty_streak >= 3:
                        log.info("3 empty pages in a row — done with %s", cat_name)
                        break
                else:
                    empty_streak = 0

                if page % 10 == 0 or page == total_pages:
                    log.info("Page %d/%d: %d entries (total: %d)", page, total_pages, len(entries), len(all_entries))
            except requests.RequestException as e:
                log.error("Failed page %d: %s", page, e)
                continue

        return all_entries


def upsert(entries, db):
    now = datetime.utcnow().isoformat()
    inserted = 0
    updated = 0
    for e in entries:
        row = db.execute("SELECT id FROM torrent_entries WHERE info_hash = ?", (e["info_hash"],)).fetchone()
        if row:
            db.execute(
                "UPDATE torrent_entries SET seeders=?, leechers=?, updated_at=? WHERE info_hash=?",
                (e["seeders"], e["leechers"], now, e["info_hash"]),
            )
            updated += 1
        else:
            db.execute(
                """INSERT INTO torrent_entries
                   (info_hash, title, category, size_bytes, size_display, seeders, leechers,
                    uploaded_at, detail_url, torrent_url, genres, image_url, category_id,
                    status, scraped_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (e["info_hash"], e["title"], e["category"], e["size_bytes"], e["size_display"],
                 e["seeders"], e["leechers"], e["uploaded_at"], e["detail_url"], e["torrent_url"],
                 e["genres"], e["image_url"], e["category_id"], "new", now, now),
            )
            inserted += 1
    db.commit()
    return inserted, updated


def _get_download_url(scraper, detail_url, info_hash):
    """Visit detail page and extract the real .torrent download link."""
    try:
        r = scraper._get(detail_url)
        links = re.findall(r'download\.php\?[^"\'<>]+', r.text)
        # Find link matching our hash
        for link in links:
            if info_hash in link.lower():
                return BASE_URL + "/" + link.replace("&amp;", "&")
        # No matching link found — try any download link on the page
        if links:
            url = BASE_URL + "/" + links[0].replace("&amp;", "&")
            log.debug("Hash %s not in links, using first: %s", info_hash[:12], url[:100])
            return url
        log.debug("No download links on %s", detail_url[:100])
    except requests.RequestException as e:
        log.debug("Failed to fetch detail page %s: %s", detail_url[:80], e)
    return None


def download_torrents(db, scraper, output_dir, category=None, limit=None):
    """Download .torrent files for entries with status='new'."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    query = "SELECT info_hash, title, category, detail_url FROM torrent_entries WHERE status = 'new'"
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY uploaded_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = db.execute(query, params).fetchall()
    log.info("Downloading .torrent files for %d entries to %s", len(rows), output_dir)

    downloaded = 0
    failed = 0
    skipped = 0
    for info_hash, title, cat, detail_url in rows:
        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)[:200]
        filename = "%s [%s].torrent" % (safe_title, info_hash[:8])
        filepath = output_dir / cat / filename

        if filepath.exists():
            db.execute("UPDATE torrent_entries SET status = 'torrent_saved', matched_path = ? WHERE info_hash = ?",
                        (str(filepath), info_hash))
            downloaded += 1
            continue

        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Get real download URL from detail page
        try:
            dl_url = _get_download_url(scraper, detail_url, info_hash)
        except Exception as e:
            log.warning("Detail page error for %s: %s", info_hash[:12], e)
            skipped += 1
            continue

        if not dl_url:
            log.debug("No download link for %s", info_hash[:12])
            skipped += 1
            continue

        try:
            scraper._throttle()
            # Referer must be ASCII-safe
            safe_referer = detail_url.encode("ascii", errors="ignore").decode("ascii")
            r = scraper.session.get(dl_url, headers={"Referer": safe_referer}, timeout=15)
            content_type = r.headers.get("content-type", "")

            if r.status_code == 200 and ("torrent" in content_type or r.content[:1] == b"d"):
                filepath.write_bytes(r.content)
                db.execute(
                    "UPDATE torrent_entries SET status = 'torrent_saved', matched_path = ?, torrent_url = ?, updated_at = ? WHERE info_hash = ?",
                    (str(filepath), dl_url, datetime.utcnow().isoformat(), info_hash),
                )
                downloaded += 1
            else:
                db.execute(
                    "UPDATE torrent_entries SET status = 'torrent_failed', updated_at = ? WHERE info_hash = ?",
                    (datetime.utcnow().isoformat(), info_hash),
                )
                failed += 1
        except requests.RequestException as e:
            log.error("Failed %s: %s", info_hash[:12], e)
            failed += 1

        total_processed = downloaded + failed + skipped
        if total_processed % 10 == 0:
            db.commit()
            log.info("Progress: %d/%d — %d ok, %d fail, %d skip",
                     total_processed, len(rows), downloaded, failed, skipped)

    db.commit()
    log.info("Done: %d downloaded, %d failed, %d skipped", downloaded, failed, skipped)
    return downloaded, failed


def print_stats(db):
    total = db.execute("SELECT COUNT(*) FROM torrent_entries").fetchone()[0]
    print("\nTotal entries: %d" % total)

    print("\nBy status:")
    for row in db.execute("SELECT status, COUNT(*) FROM torrent_entries GROUP BY status ORDER BY status"):
        print("  %-15s %6d" % row)

    print("\nBy category:")
    for row in db.execute("SELECT category, COUNT(*) FROM torrent_entries GROUP BY category ORDER BY category"):
        print("  %-15s %6d" % row)


def main():
    parser = argparse.ArgumentParser(description="Scrape sktorrent.eu catalog (standalone)")
    parser.add_argument("--user", default=os.environ.get("SKTORRENT_USER", ""))
    parser.add_argument("--password", default=os.environ.get("SKTORRENT_PASS", ""))
    parser.add_argument("--db", default=DB_PATH, help="SQLite database path")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true")
    group.add_argument("--scrape", action="store_true")
    group.add_argument("--download-torrents", action="store_true", help="Download .torrent files for all 'new' entries")
    group.add_argument("--stats", action="store_true")

    parser.add_argument("--all-categories", action="store_true")
    parser.add_argument("--category", type=int)
    parser.add_argument("--category-name", type=str, help="Filter by category name (for --download-torrents)")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--limit", type=int, help="Max entries to download")
    parser.add_argument("--torrent-dir", type=str, default="~/sktorrent_torrents", help="Dir for .torrent files")
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--output", type=str)

    args = parser.parse_args()

    db_path = args.db
    db = get_db_at(db_path)

    if args.stats:
        print_stats(db)
        return

    if not args.user or not args.password:
        print("Set SKTORRENT_USER/SKTORRENT_PASS or use --user/--password")
        sys.exit(1)

    scraper = Scraper(args.user, args.password, rate_limit=args.rate_limit)
    scraper.login()

    if args.probe:
        cat_id = args.category or 23
        html = scraper.probe(cat_id)
        if args.output:
            Path(args.output).write_text(html, encoding="utf-8")
        entries = scraper.parse_page(html, CATEGORIES.get(cat_id, "?"), cat_id)
        log.info("Parsed %d entries", len(entries))
        for e in entries[:10]:
            print("  [%2dS/%2dL] %10s  %s" % (e["seeders"], e["leechers"], e["size_display"], e["title"][:70]))
        return

    if args.download_torrents:
        torrent_dir = os.path.expanduser(args.torrent_dir)
        download_torrents(db, scraper, torrent_dir, category=args.category_name, limit=args.limit)
        print_stats(db)
        return

    if args.scrape:
        cats = {}
        if args.all_categories:
            cats = dict(CATEGORIES)
        elif args.category:
            cats = {args.category: CATEGORIES.get(args.category, "cat_%d" % args.category)}
        else:
            cats = dict(DEFAULT_CATEGORIES)

        ti, tu = 0, 0
        for cid, cname in cats.items():
            log.info("=== %s (id=%d) ===", cname, cid)
            entries = scraper.scrape_category(cid, cname, max_pages=args.max_pages)
            i, u = upsert(entries, db)
            ti += i
            tu += u
            log.info("%s: %d new, %d updated", cname, i, u)

        log.info("Done. %d new, %d updated", ti, tu)
        print_stats(db)


if __name__ == "__main__":
    main()
