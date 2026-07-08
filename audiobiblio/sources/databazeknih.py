"""
databazeknih — Client for www.databazeknih.cz book database.

Fetches book metadata for enriching Work entities with author, year, description,
genres, narrator, and cover URL.

Public interface:
  search_book(title, author=None) -> list[DbkHit]
  fetch_book(url) -> DbkBook | None
  enrich_work_from_dbk(session, work) -> EnrichReport

Routing decisions
-----------------
year:
    Work-level ORM field (in WORK_FIELDS = {"author", "year"}).
    ORM column set only when: work.year is currently None AND no MANUAL provenance
    row exists for the field. ENRICHED provenance is always recorded.

description:
    Works have no description ORM column; recorded as ENRICHED provenance on
    entity_type="work", field="description", source="databazeknih". The sync
    engine and future display layers can project this from provenance when needed.

genre:
    Genre is absent from WORK_FIELDS by design (it lives on Episode in the UI and
    tag layout). ENRICHED genre MetadataValue rows are recorded for each episode of
    the work. The genre value is a comma-joined string of genres from the book page.

narrator:
    Same routing as genre — recorded per episode, not per work.
    If book.narrator is None (standard book pages don't expose narrator), no rows
    are written.

Cache:
    Raw DbkBook fields are stored in work.extra["dbk"] via dict reassignment
    ({**(work.extra or {}), "dbk": {...}}) — never mutating the existing dict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import quote_plus

import requests
import structlog
from bs4 import BeautifulSoup

from audiobiblio.core.db.models import FieldOrigin, Work
from audiobiblio.core.provenance import has_manual, record_value
from audiobiblio.core.ratelimit import RateLimiter

log = structlog.get_logger()

_BASE_URL = "https://www.databazeknih.cz"
_UA = "audiobiblio/0.5 (personal audiobook manager)"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "cs,en;q=0.5"}

# Module-level rate limiter: max 1 request every 2 seconds.
_dbk_limiter = RateLimiter(rate=0.5, burst=1)

_FUZZY_THRESHOLD = 0.85


@dataclass(frozen=True)
class DbkHit:
    """One search result hit from databazeknih.cz."""

    url: str
    title: str
    author: Optional[str]


@dataclass
class DbkBook:
    """Parsed metadata from a databazeknih.cz book detail page."""

    title: str
    author: Optional[str]
    year: Optional[int]
    description: Optional[str]
    genres: list[str]
    narrator: Optional[str]
    cover_url: Optional[str]


@dataclass
class EnrichReport:
    """Result of an enrich_work_from_dbk call."""

    skipped: bool = False
    reason: Optional[str] = None
    fields_set: list[str] = field(default_factory=list)
    source_url: Optional[str] = None


def _parse_search_hits(html: str) -> list[DbkHit]:
    """Parse a databazeknih.cz search results page into DbkHit objects.

    Search result links use:
        <a class="new" href="/prehled-knihy/SLUG" type="book">TITLE</a>
    Followed (after a <br>) by:
        <span class="pozn">YEAR,\\nAUTHOR</span>

    Returns [] on parse error (logs warning, never raises).
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        hits: list[DbkHit] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", {"class": "new", "type": "book"}):
            href = a.get("href", "")
            if not href.startswith("/prehled-knihy/"):
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            url = _BASE_URL + href
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Author is in the following <span class="pozn">: "YEAR,\nAUTHOR"
            pozn = a.find_next_sibling("span", class_="pozn")
            author: Optional[str] = None
            if pozn:
                raw = pozn.get_text(separator=" ").replace("\n", " ").strip()
                # Format: "2007, Karel Čapek" — strip leading year + comma
                m = re.match(r"^\d{4}\s*,\s*(.+)$", raw)
                if m:
                    author = m.group(1).strip() or None

            hits.append(DbkHit(url=url, title=title, author=author))

        return hits
    except Exception as e:
        log.warning("dbk_parse_search_hits_failed", error=str(e))
        return []


def _parse_book_page(html: str) -> Optional[DbkBook]:
    """Parse a databazeknih.cz book detail page into a DbkBook object.

    Field locations on /prehled-knihy/SLUG:
      title:       <h1 class="oddown_zero"> inside div#bookDetail
      author:      <span class="author"><a href="/autori/...">
      year:        first 4-digit year (1500-2099) in <div class="lora lineHeightMid">
      description: first <p class="new2 odtop"> in div#left (links stripped)
      genres:      <a href="/zanry/..."> inside <div class="lora lineHeightMid">
      cover_url:   <meta property="og:image">
      narrator:    not present on standard book pages (returns None)
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Title
        h1 = soup.find("h1", class_="oddown_zero")
        title = h1.get_text(strip=True) if h1 else None
        if not title:
            log.warning("dbk_parse_book_no_title")
            return None

        # Author: first <span class="author"> → <a>
        author: Optional[str] = None
        author_span = soup.find("span", class_="author")
        if author_span:
            a_tag = author_span.find("a")
            if a_tag:
                author = a_tag.get_text(strip=True) or None

        # Year and genres from <div class="lora lineHeightMid">
        year: Optional[int] = None
        genres: list[str] = []
        lora_div = soup.find(
            "div", class_=lambda c: c and "lora" in c and "lineHeightMid" in c
        )
        if lora_div:
            # Genres: all /zanry/ links
            genres = [
                a.get_text(strip=True)
                for a in lora_div.find_all("a", href=re.compile(r"/zanry/"))
                if a.get_text(strip=True)
            ]
            # Year: first 4-digit number in a plausible book-year range
            year_match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", lora_div.get_text())
            if year_match:
                year = int(year_match.group(1))

        # Description: first <p class="new2 odtop"> in div#left
        description: Optional[str] = None
        left_div = soup.find("div", id="left")
        if left_div:
            desc_p = left_div.find(
                "p",
                class_=lambda c: c and "new2" in c and "odtop" in c,
            )
            if desc_p:
                # Remove "... celý text" anchors before extracting text
                for a in list(desc_p.find_all("a")):
                    a.extract()
                description = desc_p.get_text(separator=" ").strip() or None

        # Cover URL: <meta property="og:image">
        cover_url: Optional[str] = None
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            cover_url = og_img["content"]

        # Narrator: not present on standard book pages
        narrator: Optional[str] = None

        return DbkBook(
            title=title,
            author=author,
            year=year,
            description=description,
            genres=genres,
            narrator=narrator,
            cover_url=cover_url,
        )
    except Exception as e:
        log.warning("dbk_parse_book_failed", error=str(e))
        return None


def search_book(title: str, author: Optional[str] = None) -> list[DbkHit]:
    """Search databazeknih.cz for a book by title (and optionally author).

    Applies the module-level rate limiter (1 req/2 s).
    Returns [] on HTTP or parse error (logs warning, never raises).
    """
    query = f"{title} {author}" if author else title
    url = f"{_BASE_URL}/search?q={quote_plus(query)}&in=books"
    _dbk_limiter.wait()
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.warning("dbk_search_http_failed", query=query, error=str(e))
        return []
    return _parse_search_hits(r.text)


def fetch_book(url: str) -> Optional[DbkBook]:
    """Fetch and parse a databazeknih.cz book detail page.

    Applies the module-level rate limiter (1 req/2 s).
    Returns None on HTTP or parse error (logs warning, never raises).
    """
    _dbk_limiter.wait()
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30, allow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.warning("dbk_fetch_http_failed", url=url, error=str(e))
        return None
    return _parse_book_page(r.text)


def _similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _best_hit(
    hits: list[DbkHit], title: str, author: Optional[str]
) -> tuple[Optional[DbkHit], float]:
    """Return (best_hit, score) for the hit that best matches title + author.

    Score = average of title_similarity and author_similarity when both are
    available; title_similarity alone otherwise.
    """
    best: Optional[DbkHit] = None
    best_score = 0.0
    for hit in hits:
        t_ratio = _similarity(title, hit.title)
        if author and hit.author:
            a_ratio = _similarity(author, hit.author)
            score = (t_ratio + a_ratio) / 2
        else:
            score = t_ratio
        if score > best_score:
            best_score = score
            best = hit
    return best, best_score


def enrich_work_from_dbk(session, work: Work) -> EnrichReport:
    """Enrich a Work with metadata from databazeknih.cz.

    Routing decisions — see module docstring for rationale.

    Does not raise. On network failure or low fuzzy-match score, returns an
    EnrichReport with skipped=True and a human-readable reason.
    """
    report = EnrichReport()

    hits = search_book(work.title, work.author)
    if not hits:
        report.skipped = True
        report.reason = "no search results"
        log.info("dbk_enrich_no_results", work_id=work.id, title=work.title)
        return report

    best, score = _best_hit(hits, work.title, work.author)
    if best is None or score < _FUZZY_THRESHOLD:
        report.skipped = True
        report.reason = f"ambiguous (best score {score:.2f} < {_FUZZY_THRESHOLD})"
        log.info(
            "dbk_enrich_ambiguous",
            work_id=work.id,
            title=work.title,
            best_hit=best.title if best else None,
            score=round(score, 2),
        )
        return report

    report.source_url = best.url
    log.info(
        "dbk_enrich_matched",
        work_id=work.id,
        title=work.title,
        hit_title=best.title,
        score=round(score, 2),
        url=best.url,
    )

    book = fetch_book(best.url)
    if book is None:
        report.skipped = True
        report.reason = "fetch failed"
        return report

    # Cache raw result in work.extra["dbk"] (dict reassignment — no in-place mutation)
    work.extra = {
        **(work.extra or {}),
        "dbk": {
            "url": best.url,
            "title": book.title,
            "author": book.author,
            "year": book.year,
            "description": book.description,
            "genres": book.genres,
            "narrator": book.narrator,
            "cover_url": book.cover_url,
        },
    }

    # year (work-level): record ENRICHED provenance; update ORM only when safe
    if book.year is not None:
        record_value(
            session,
            entity_type="work",
            entity_id=work.id,
            field="year",
            value=str(book.year),
            origin=FieldOrigin.ENRICHED,
            source="databazeknih",
        )
        if work.year is None and not has_manual(session, "work", work.id, "year"):
            work.year = book.year
        report.fields_set.append("year")

    # description (work-level, provenance-only — no ORM column on Work)
    if book.description:
        record_value(
            session,
            entity_type="work",
            entity_id=work.id,
            field="description",
            value=book.description,
            origin=FieldOrigin.ENRICHED,
            source="databazeknih",
        )
        report.fields_set.append("description")

    # genre + narrator: episode-level (genre absent from WORK_FIELDS by design)
    genre_str = ", ".join(book.genres) if book.genres else None
    for ep in work.episodes:
        if genre_str:
            record_value(
                session,
                entity_type="episode",
                entity_id=ep.id,
                field="genre",
                value=genre_str,
                origin=FieldOrigin.ENRICHED,
                source="databazeknih",
            )
        if book.narrator:
            record_value(
                session,
                entity_type="episode",
                entity_id=ep.id,
                field="narrator",
                value=book.narrator,
                origin=FieldOrigin.ENRICHED,
                source="databazeknih",
            )

    if genre_str:
        report.fields_set.append("genre")
    if book.narrator:
        report.fields_set.append("narrator")

    session.commit()
    return report
