"""
html_scraper — Extract metadata from saved rozhlas.cz / mujrozhlas.cz HTML files.

Extracts: synopsis (perex + description), cast/crew, premiere date.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()


@dataclass
class ScrapedMeta:
    """Metadata extracted from a saved episode HTML page."""
    perex: str = ""
    description: str = ""
    performer: str = ""  # Účinkuje/Účinkují
    director: str = ""   # Režie
    dramaturgy: str = "" # Dramaturgie / Připravil(a)
    premiere: str = ""   # Premiéra date
    source_url: str = ""


def scrape_episode_html(html_path: Path | str) -> ScrapedMeta:
    """Parse a saved rozhlas.cz/mujrozhlas.cz HTML file for episode metadata."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("beautifulsoup4_not_installed")
        return ScrapedMeta()

    html_path = Path(html_path)
    if not html_path.exists():
        return ScrapedMeta()

    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
    except Exception as e:
        log.error("html_parse_failed", path=str(html_path), error=str(e))
        return ScrapedMeta()

    meta = ScrapedMeta()
    body_text = soup.get_text()

    # Perex (short synopsis)
    perex_el = soup.find(class_=re.compile(r"perex", re.I))
    if perex_el:
        meta.perex = perex_el.get_text(strip=True)

    # Description paragraphs (longer synopsis) — only the first relevant one
    # after the perex, which is typically the episode-specific description.
    # Later paragraphs are often about other shows on the same page.
    desc_parts = []
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if t and len(t) > 50 and "cookie" not in t.lower() and "osobní údaje" not in t.lower():
            if t == meta.perex:
                continue
            desc_parts.append(t)
            # Take only the first description paragraph after perex
            break
    meta.description = "\n\n".join(desc_parts)

    # Structured fields: Účinkuje, Režie, Premiéra, Dramaturgie
    _field_patterns = {
        "performer": [r"Účinkuj[eí]:", r"Účinkují:"],
        "director": [r"Režie:"],
        "dramaturgy": [r"Dramaturgie:", r"Připravil[a]?:"],
        "premiere": [r"Premiéra:"],
    }
    for attr, patterns in _field_patterns.items():
        for pattern in patterns:
            match = re.search(pattern + r"\s*(.+?)(?:\s{2,}|\n\s*\n|\n\s*[A-ZČŘŠŽŤĎŇÁÉÍÓÚŮÝ])", body_text)
            if match:
                setattr(meta, attr, match.group(1).strip())
                break

    return meta


def build_comment(
    author: str,
    full_title: str,
    subtitle: str,
    scraped: ScrapedMeta,
    extra_urls: list[str] | None = None,
) -> str:
    """Build the rich comment field.

    Format (diacritics preserved):
        Author: Full Title
        Subtitle
        <blank line>
        Synopsis from rozhlas.cz
        <blank line>
        Účinkuje: ...
        Režie: ...
        Premiéra: ...
        <blank line>
        Source URL
    """
    lines = []

    # Header: Author - Full Title (with diacritics)
    if author and full_title:
        lines.append(f"{author}: {full_title}")
    elif full_title:
        lines.append(full_title)

    if subtitle:
        lines.append(subtitle)

    # Synopsis
    synopsis = scraped.perex or scraped.description
    if synopsis:
        lines.append("")
        lines.append(synopsis)

    # Additional description if different from perex
    if scraped.description and scraped.description != scraped.perex:
        lines.append("")
        lines.append(scraped.description)

    # Cast/crew
    crew_lines = []
    if scraped.performer:
        crew_lines.append(f"Účinkuje: {scraped.performer}")
    if scraped.dramaturgy:
        crew_lines.append(f"Dramaturgie: {scraped.dramaturgy}")
    if scraped.director:
        crew_lines.append(f"Režie: {scraped.director}")
    if scraped.premiere:
        crew_lines.append(f"Premiéra: {scraped.premiere}")

    if crew_lines:
        lines.append("")
        lines.extend(crew_lines)

    # Source URLs
    urls = extra_urls or []
    if urls:
        lines.append("")
        lines.extend(urls)

    return "\n".join(lines)
