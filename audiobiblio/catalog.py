"""
catalog — Scrape reference sources for complete episode catalogs.

Supports Wikipedia episode tables and mluvenypanacek.cz episode lists.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import requests
import structlog
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from .db.models import CatalogEntry, CatalogStatus

log = structlog.get_logger()

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Czech month names → month numbers
_CZ_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4,
    "května": 5, "června": 6, "července": 7, "srpna": 8,
    "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}


def _parse_cz_date(text: str) -> Optional[datetime]:
    """Parse Czech date like '9. 1. 2010' or '9. ledna 2010'."""
    text = text.strip().rstrip(".")
    # Try numeric format: DD. MM. YYYY
    m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    # Try named month: DD. mesice YYYY
    m = re.match(r"(\d{1,2})\.\s*(\w+)\s+(\d{4})", text)
    if m:
        month = _CZ_MONTHS.get(m.group(2).lower())
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                return None
    return None


def _fetch_html(url: str) -> str:
    """Fetch HTML with browser User-Agent."""
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    resp.raise_for_status()
    return resp.text


def scrape_catalog(program_id: int, source: str, url: str) -> list[dict]:
    """Dispatch to source-specific scraper."""
    if source == "wikipedia":
        return _scrape_wikipedia(url)
    elif source == "mluvenypanacek":
        return _scrape_mluvenypanacek(url)
    else:
        raise ValueError(f"Unknown catalog source: {source}")


def _scrape_wikipedia(url: str) -> list[dict]:
    """Parse Wikipedia episode tables.

    Tables have dual-column layout:
    Epizoda | Premiéra | Info | (spacer) | Epizoda | Premiéra | Info
    Row cells: [number, title, date, ref, number2, title2, date2, ref2]
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        # Check header: skip tables that don't have the expected dual-column layout
        header_cells = rows[0].find_all(["th", "td"])
        header_texts = [c.get_text(strip=True).lower() for c in header_cells]
        # Expect headers like: epizoda, premiéra, info, (spacer), epizoda, premiéra, info
        if len(header_texts) < 7 or header_texts.count("epizoda") < 2:
            continue
        # Skip header row
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) < 3:
                continue
            # Parse left side
            _parse_wiki_episode(texts, 0, entries)
            # Parse right side (if dual-column)
            if len(texts) >= 7:
                _parse_wiki_episode(texts, 4, entries)

    log.info("wikipedia_scraped", url=url, count=len(entries))
    return entries


def _parse_wiki_episode(texts: list[str], offset: int, entries: list[dict]) -> None:
    """Parse one episode from a Wikipedia table row at the given column offset."""
    try:
        num_text = texts[offset].strip()
        if not num_text:
            return
        # Episode number: might be like "1" or "(0.)"
        num_match = re.match(r"\(?\d+\)?\.?", num_text)
        if not num_match:
            return
        ep_num = int(re.sub(r"[^\d]", "", num_text))
        title = texts[offset + 1].strip()
        if not title:
            return
        date_text = texts[offset + 2].strip() if offset + 2 < len(texts) else ""
        air_date = _parse_cz_date(date_text)
        year = air_date.year if air_date else None

        entries.append({
            "episode_number": ep_num,
            "title": title,
            "air_date": air_date,
            "year": year,
            "author": None,
        })
    except (IndexError, ValueError):
        pass


def _scrape_mluvenypanacek(url: str) -> list[dict]:
    """Parse mluvenypanacek.cz episode list.

    Format: inline entries like:
    NUMBER. <a>TITLE</a>. Premiéra DD. MM. YYYY.
    Separated by ' – ' dashes.
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    storycontent = soup.find("div", class_="storycontent")
    if not storycontent:
        log.warning("mluvenypanacek_no_storycontent", url=url)
        return entries

    # Get the raw HTML of storycontent and parse episode entries
    # Pattern: NUMBER. <a>TITLE</a>. Premiéra DATE.
    # Entries are separated by ' – '
    raw_html = str(storycontent)

    # Find all episodes by looking for the pattern: number. <a ...>title</a>. Premiéra date
    pattern = re.compile(
        r"(?:^|\s|–\s*)"  # start or after dash separator
        r"\(?(\d+)\)?\.?\s*"  # episode number (optionally in parens)
        r'<a[^>]*>([^<]+)</a>'  # linked title
        r"\.\s*Premi[eé]ra\s+"  # "Premiéra" marker
        r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})"  # date DD. MM. YYYY
    )

    for m in pattern.finditer(raw_html):
        ep_num = int(m.group(1))
        title = m.group(2).strip()
        air_date = _parse_cz_date(m.group(3))
        year = air_date.year if air_date else None

        entries.append({
            "episode_number": ep_num,
            "title": title,
            "air_date": air_date,
            "year": year,
            "author": None,
        })

    log.info("mluvenypanacek_scraped", url=url, count=len(entries))
    return entries


def upsert_catalog(
    session: Session,
    program_id: int,
    entries: list[dict],
    source: str,
    source_url: str | None = None,
) -> dict:
    """Insert/update catalog entries, deduplicating by (program_id, episode_number, title).

    Returns dict with counts: {inserted, updated, total}.
    """
    inserted = 0
    updated = 0

    for entry in entries:
        ep_num = entry.get("episode_number")
        title = entry.get("title", "").strip()
        if not title:
            continue

        # Look for existing entry by episode_number first, then title
        existing = None
        if ep_num is not None:
            existing = session.query(CatalogEntry).filter(
                CatalogEntry.program_id == program_id,
                CatalogEntry.episode_number == ep_num,
            ).first()

        if not existing:
            existing = session.query(CatalogEntry).filter(
                CatalogEntry.program_id == program_id,
                CatalogEntry.title == title,
            ).first()

        if existing:
            # Update fields that are missing
            if entry.get("air_date") and not existing.air_date:
                existing.air_date = entry["air_date"]
            if entry.get("author") and not existing.author:
                existing.author = entry["author"]
            if entry.get("year") and not existing.year:
                existing.year = entry["year"]
            if source_url and not existing.source_url:
                existing.source_url = source_url
            existing.updated_at = datetime.utcnow()
            updated += 1
        else:
            new_entry = CatalogEntry(
                program_id=program_id,
                episode_number=ep_num,
                title=title,
                author=entry.get("author"),
                year=entry.get("year"),
                air_date=entry.get("air_date"),
                source=source,
                source_url=source_url,
                status=CatalogStatus.MISSING,
            )
            session.add(new_entry)
            inserted += 1

    session.commit()
    log.info("catalog_upserted", program_id=program_id, source=source,
             inserted=inserted, updated=updated)
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}
