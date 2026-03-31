#!/usr/bin/env python3
"""
Scrape torrent catalog from sktorrent.eu into the audiobiblio database.

Usage:
    # Probe mode — dump raw HTML to debug parsing
    python scripts/sktorrent_scraper.py --probe
    python scripts/sktorrent_scraper.py --probe --output /tmp/probe.html

    # Scrape books + audiobooks (categories 23 + 24)
    python scripts/sktorrent_scraper.py --scrape

    # Scrape specific category
    python scripts/sktorrent_scraper.py --scrape --category 23

    # Scrape all categories
    python scripts/sktorrent_scraper.py --scrape --all-categories

    # Limit pages (for testing)
    python scripts/sktorrent_scraper.py --scrape --max-pages 3

    # Show stats
    python scripts/sktorrent_scraper.py --stats

Credentials: set SKTORRENT_USER and SKTORRENT_PASS env vars,
or pass --user / --password on the command line.
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audiobiblio.db.session import get_session
from audiobiblio.db.models import TorrentEntry, TorrentStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sktorrent")

BASE_URL = "https://sktorrent.eu/torrent"

# Category IDs from the site
CATEGORIES = {
    # Books & audio
    23: "knihy",         # Knihy a Časopisy
    24: "mluvene_slovo", # Mluvené slovo (audiobooks)
    # Films
    1: "filmy_cz",       # Filmy CZ/SK dabing
    5: "rozpravky",      # Filmy Kreslené
    14: "filmy_kamera",  # Filmy Kamera
    15: "filmy_tit",     # Filmy s titulkama
    20: "filmy_dvd",     # Filmy DVD
    31: "filmy_bez_tit", # Filmy bez titulků
    # HD
    3: "filmy_3d",       # 3D Filmy
    19: "filmy_hd",      # HD Filmy
    28: "bluray",        # Blu-ray Filmy
    29: "bluray_3d",     # 3D Blu-ray Filmy
    43: "uhd",           # UHD Filmy
    # TV
    16: "serial",        # Seriál
    17: "dokument",      # Dokument
    42: "tv_porad",      # TV Pořad
    44: "sport",         # Sport
    # Audio
    2: "hudba",          # Hudba
    22: "hudba_dj",      # Hudba DJ's Mix
    26: "hudebni_videa", # Hudební videa
    45: "soundtrack",    # Soundtrack
    # Games & software
    18: "hry_win",       # Hry na Windows
    30: "hry_konzole",   # Hry na Konzole
    37: "hry_linux",     # Hry na Linux
    59: "hry_mac",       # Hry na Mac
    60: "hry_xxx",       # xXx hry
    63: "hry_vr",        # VR Hry
    21: "programy",      # Programy
    27: "mobil",         # Mobil, PDA
    # Other
    9: "xxx",
    25: "ostatni",       # Ostatní
}

# Default categories to scrape (books + audiobooks)
DEFAULT_CATEGORIES = {23: "knihy", 24: "mluvene_slovo"}


@dataclass
class ScrapedTorrent:
    """Parsed torrent entry from a listing page."""
    title: str
    info_hash: str
    detail_url: str
    category_name: str
    category_id: int
    size_display: str
    size_bytes: Optional[int]
    seeders: int
    leechers: int
    uploaded_at: Optional[datetime]
    genres: str  # comma-separated genre tags
    image_url: str


def _parse_size(size_str: str) -> Optional[int]:
    """Convert '4.8 GB' or '611.8 MB' to bytes."""
    m = re.match(r"([\d.,]+)\s*(GB|MB|KB|TB)", size_str.strip(), re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(val * multipliers.get(unit, 1))


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse date like '31/03/2026' or '31.03.2026'."""
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _extract_hash_from_url(url: str) -> Optional[str]:
    """Extract info_hash from detail URL like details.php?...&id=HASH."""
    m = re.search(r"[&?]id=([a-fA-F0-9]{40})", url)
    return m.group(1).lower() if m else None


class SktorrentScraper:
    def __init__(self, username: str, password: str, rate_limit: float = 1.0):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        })
        self.username = username
        self.password = password
        self.rate_limit = rate_limit
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()

    def _get(self, url: str) -> requests.Response:
        self._throttle()
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r

    def login(self) -> bool:
        """Log in to the tracker. Returns True on success."""
        self._throttle()
        # Fetch homepage for cookies
        self.session.get(f"{BASE_URL}/", timeout=30)

        self._throttle()
        r = self.session.post(
            f"{BASE_URL}/takelogin.php",
            data={"username": self.username, "password": self.password},
            allow_redirects=True,
            timeout=30,
        )
        if "logout" in r.text.lower() or self.username.lower() in r.text.lower():
            log.info("Logged in as %s", self.username)
            return True

        log.warning("Login may have failed — continuing anyway")
        return False

    def probe(self, category_id: int = 23) -> str:
        """Fetch a single category page and return raw HTML."""
        url = f"{BASE_URL}/torrents_v2.php?active=0&order=data&by=DESC&category={category_id}&page=0"
        log.info("Probing %s", url)
        r = self._get(url)
        return r.text

    def get_total_pages(self, html: str) -> int:
        """Extract max page number from pagination links."""
        page_nums = set()
        for m in re.finditer(r"page=(\d+)", html):
            page_nums.add(int(m.group(1)))
        return max(page_nums) if page_nums else 0

    def parse_listing_page(
        self, html: str, category_name: str, category_id: int
    ) -> list[ScrapedTorrent]:
        """
        Parse torrent entries from the v2 (image) listing page.

        Structure per entry (inside <td class="lista">):
          - Category link: <a href="...category=X">Category Name</a>
          - Genre links: <a href="...zaner=Drama">Drama</a> / ...
          - Detail link: <a href="details.php?name=...&id=HASH" title="...">
              <img ...><br>TITLE</a>
          - Text: Velkost X.X GB | Pridany DD/MM/YYYY
          - Text: Odosielaju : N
          - Text: Stahuju : N
        """
        soup = BeautifulSoup(html, "html.parser")
        entries = []

        # Find all detail links
        detail_links = soup.find_all("a", href=re.compile(r"details\.php\?.*id=[a-fA-F0-9]{40}"))

        for link in detail_links:
            href = link.get("href", "")
            info_hash = _extract_hash_from_url(href)
            if not info_hash:
                continue

            # Title is the text after <br> inside the link (after the image)
            # The link contains: <img ...><br>TITLE TEXT
            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                # Fallback: extract from title attribute
                title_attr = link.get("title", "")
                # Strip "Stiahni si CATEGORY " prefix
                title = re.sub(r"^Stiahni si .+? ", "", title_attr, count=1)

            if not title:
                continue

            # Get the parent <td> cell for context
            cell = link.find_parent("td")
            if not cell:
                continue

            cell_text = cell.get_text(" ", strip=True)

            # Extract size: "Velkost X.X GB"
            size_display = ""
            size_bytes = None
            size_match = re.search(r"Velkost\s+([\d.,]+\s*(?:GB|MB|KB|TB))", cell_text, re.IGNORECASE)
            if size_match:
                size_display = size_match.group(1)
                size_bytes = _parse_size(size_display)

            # Extract date: "Pridany DD/MM/YYYY"
            uploaded_at = None
            date_match = re.search(r"Pridany\s+(\d{2}/\d{2}/\d{4})", cell_text)
            if date_match:
                uploaded_at = _parse_date(date_match.group(1))

            # Extract seeders: "Odosielaju : N"
            seeders = 0
            seed_match = re.search(r"Odosielaju\s*:\s*(\d+)", cell_text)
            if seed_match:
                seeders = int(seed_match.group(1))

            # Extract leechers: "Stahuju : N"
            leechers = 0
            leech_match = re.search(r"Stahuju\s*:\s*(\d+)", cell_text)
            if leech_match:
                leechers = int(leech_match.group(1))

            # Extract genres from genre links
            genre_links = cell.find_all("a", href=re.compile(r"zaner="))
            genres = ", ".join(a.get_text(strip=True) for a in genre_links)

            # Extract image URL
            img = link.find("img")
            image_url = ""
            if img:
                image_url = img.get("data-src", "") or img.get("src", "")

            # Build full detail URL
            detail_url = href if href.startswith("http") else f"{BASE_URL}/{href}"

            entries.append(ScrapedTorrent(
                title=title,
                info_hash=info_hash,
                detail_url=detail_url,
                category_name=category_name,
                category_id=category_id,
                size_display=size_display,
                size_bytes=size_bytes,
                seeders=seeders,
                leechers=leechers,
                uploaded_at=uploaded_at,
                genres=genres,
                image_url=image_url,
            ))

        return entries

    def scrape_category(
        self,
        category_id: int,
        category_name: str,
        max_pages: Optional[int] = None,
    ) -> list[ScrapedTorrent]:
        """Scrape all pages of a category."""
        all_entries: list[ScrapedTorrent] = []

        # Fetch first page
        url = f"{BASE_URL}/torrents_v2.php?active=0&order=data&by=DESC&category={category_id}&page=0"
        log.info("Fetching first page: %s", url)
        r = self._get(url)
        total_pages = self.get_total_pages(r.text)

        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        log.info("Category %s (id=%d): %d pages to scrape", category_name, category_id, total_pages + 1)

        # Parse first page
        entries = self.parse_listing_page(r.text, category_name, category_id)
        all_entries.extend(entries)
        log.info("Page 0: %d entries (total: %d)", len(entries), len(all_entries))

        # Remaining pages
        for page in range(1, total_pages + 1):
            url = f"{BASE_URL}/torrents_v2.php?active=0&order=data&by=DESC&category={category_id}&page={page}"
            try:
                r = self._get(url)
                entries = self.parse_listing_page(r.text, category_name, category_id)
                all_entries.extend(entries)
                if page % 10 == 0 or page == total_pages:
                    log.info("Page %d/%d: %d entries (total: %d)", page, total_pages, len(entries), len(all_entries))
            except requests.RequestException as e:
                log.error("Failed page %d: %s", page, e)
                continue

        return all_entries


def upsert_entries(entries: list[ScrapedTorrent], db=None) -> tuple[int, int]:
    """Insert or update torrent entries in the database. Returns (inserted, updated)."""
    if db is None:
        db = get_session()

    inserted = 0
    updated = 0

    for entry in entries:
        existing = db.query(TorrentEntry).filter_by(info_hash=entry.info_hash).first()

        if existing:
            existing.seeders = entry.seeders
            existing.leechers = entry.leechers
            existing.updated_at = datetime.utcnow()
            updated += 1
        else:
            torrent_url = (
                f"{BASE_URL}/download.php?id={entry.info_hash}"
                f"&f={entry.title.replace(' ', '%20')}.torrent"
            )
            row = TorrentEntry(
                info_hash=entry.info_hash,
                title=entry.title,
                category=entry.category_name,
                size_bytes=entry.size_bytes,
                size_display=entry.size_display,
                seeders=entry.seeders,
                leechers=entry.leechers,
                uploaded_at=entry.uploaded_at,
                detail_url=entry.detail_url,
                torrent_url=torrent_url,
                status=TorrentStatus.NEW,
                extra={
                    "genres": entry.genres,
                    "image_url": entry.image_url,
                    "category_id": entry.category_id,
                },
            )
            db.add(row)
            inserted += 1

    db.commit()
    return inserted, updated


def print_stats(db=None) -> None:
    """Print catalog statistics."""
    if db is None:
        db = get_session()

    total = db.query(TorrentEntry).count()
    by_status = {}
    for status in TorrentStatus:
        count = db.query(TorrentEntry).filter_by(status=status.value).count()
        if count:
            by_status[status.value] = count

    by_category = {}
    for row in db.query(TorrentEntry.category).distinct():
        cat = row[0]
        count = db.query(TorrentEntry).filter_by(category=cat).count()
        by_category[cat] = count

    print(f"\nTotal entries: {total}")
    print("\nBy status:")
    for status, count in sorted(by_status.items()):
        print(f"  {status:<15} {count:>6}")
    print("\nBy category:")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat:<15} {count:>6}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape sktorrent.eu catalog")
    parser.add_argument("--user", default=os.environ.get("SKTORRENT_USER", ""))
    parser.add_argument("--password", default=os.environ.get("SKTORRENT_PASS", ""))

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true", help="Dump raw HTML for debugging")
    group.add_argument("--scrape", action="store_true", help="Scrape and store to DB")
    group.add_argument("--stats", action="store_true", help="Show catalog stats")

    parser.add_argument("--all-categories", action="store_true", help="Scrape all categories")
    parser.add_argument("--category", type=int, help="Single category ID to scrape")
    parser.add_argument("--max-pages", type=int, help="Limit pages per category (for testing)")
    parser.add_argument("--rate-limit", type=float, default=1.0, help="Seconds between requests")
    parser.add_argument("--output", type=str, help="Output file for probe HTML")

    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if not args.user or not args.password:
        print("Set SKTORRENT_USER and SKTORRENT_PASS env vars, or use --user/--password")
        sys.exit(1)

    scraper = SktorrentScraper(args.user, args.password, rate_limit=args.rate_limit)
    scraper.login()

    if args.probe:
        cat_id = args.category or 23
        html = scraper.probe(cat_id)
        if args.output:
            Path(args.output).write_text(html, encoding="utf-8")
            log.info("Saved %d bytes to %s", len(html), args.output)
        else:
            print(html[:2000])
            print("... (truncated, use --output to save full HTML)")

        cat_name = CATEGORIES.get(cat_id, f"cat_{cat_id}")
        entries = scraper.parse_listing_page(html, cat_name, cat_id)
        log.info("Parsed %d entries from probe page", len(entries))
        for e in entries[:10]:
            print(f"  [{e.seeders:>2}S/{e.leechers:>2}L] {e.size_display:>10}  {e.title[:70]}")
        return

    if args.scrape:
        categories_to_scrape: dict[int, str]
        if args.all_categories:
            categories_to_scrape = dict(CATEGORIES)
        elif args.category:
            cat_id = args.category
            categories_to_scrape = {cat_id: CATEGORIES.get(cat_id, f"cat_{cat_id}")}
        else:
            categories_to_scrape = dict(DEFAULT_CATEGORIES)

        total_inserted = 0
        total_updated = 0

        for cat_id, cat_name in categories_to_scrape.items():
            log.info("=== Scraping category: %s (id=%d) ===", cat_name, cat_id)
            entries = scraper.scrape_category(cat_id, cat_name, max_pages=args.max_pages)
            inserted, updated = upsert_entries(entries)
            total_inserted += inserted
            total_updated += updated
            log.info("Category %s: %d inserted, %d updated", cat_name, inserted, updated)

        log.info("Done. Total: %d inserted, %d updated", total_inserted, total_updated)
        print_stats()


if __name__ == "__main__":
    main()
