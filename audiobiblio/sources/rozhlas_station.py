"""rozhlas.cz station-site discovery.

Station sites expose the TRUE content hierarchy the aggregator flattens:

    stanice  olomouc.rozhlas.cz
    pořad    olomouc.rozhlas.cz/poctenicko-6370902
    kniha    olomouc.rozhlas.cz/anna-strnadova-...-9617888   (12 dílů)

Program pages are HTML listings yt-dlp cannot extract; their article links
(slug ending in a 7+ digit id) are the books. Book pages ARE extractable
(RozhlasVltava extractor) and carry per-part entry ids.
"""
from __future__ import annotations

import re
import ssl
import urllib.request
from html import unescape
from urllib.parse import urljoin, urlparse

_ARTICLE_RE = re.compile(r'href="((?:https?://[a-z0-9.-]+)?/[a-z0-9-]+-\d{7,})"')
_TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>|<title>(.*?)</title>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_SSL_CTX = ssl.create_default_context()


def is_station_program_url(url: str | None) -> bool:
    """True for `<sub>.rozhlas.cz` pages (NOT the mujrozhlas aggregator)."""
    if not url:
        return False
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return False
    return netloc.endswith(".rozhlas.cz") and "mujrozhlas" not in netloc


def discover_articles(html: str, base_url: str) -> list[str]:
    """Absolute article urls (slug-NNNNNNN) found on a station page.

    Deduped, same host only, the page's own url excluded. Non-audio pages
    (contact pages etc. share the id pattern) are NOT filtered here — the
    caller's probe step drops pages yt-dlp cannot extract.
    """
    base_host = urlparse(base_url).netloc.lower()
    seen: set[str] = set()
    out: list[str] = []
    for m in _ARTICLE_RE.finditer(html):
        url = urljoin(base_url, m.group(1))
        if urlparse(url).netloc.lower() != base_host:
            continue
        if url.rstrip("/") == base_url.rstrip("/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


from dataclasses import dataclass
from datetime import datetime

_CZECH_MONTHS = {
    "leden": 1, "ledna": 1, "únor": 2, "února": 2, "březen": 3, "března": 3,
    "duben": 4, "dubna": 4, "květen": 5, "května": 5, "červen": 6, "června": 6,
    "červenec": 7, "července": 7, "srpen": 8, "srpna": 8, "září": 9,
    "říjen": 10, "října": 10, "listopad": 11, "listopadu": 11,
    "prosinec": 12, "prosince": 12,
}

_CARD_SPLIT_RE = re.compile(r'<li class="b-022__list-item')
_HEADING_RE = re.compile(r'<h\d[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE_RE = re.compile(r'class="[^"]*date[^"]*"[^>]*>([^<]+)<')
_PEREX_RE = re.compile(r"<p[^>]*>([^<]{30,600})</p>")
_LAST_PAGE_RE = re.compile(r"page=(\d+)")


@dataclass(frozen=True)
class ArticleStub:
    """One archive card: everything known about an episode WITHOUT touching
    yt-dlp — enough to index aired episodes whose audio is no longer online
    (air date + annotation drive later reconstruction from re-airs)."""
    url: str
    title: str
    published_at: datetime | None
    perex: str | None


def parse_czech_date(text: str) -> datetime | None:
    """'20. červenec 2026' → datetime (naive)."""
    m = re.search(r"(\d{1,2})\.\s*(\S+)\s*(\d{4})", text or "")
    if not m:
        return None
    month = _CZECH_MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return datetime(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def discover_article_stubs(html: str, base_url: str) -> list[ArticleStub]:
    """Parse archive cards: heading link+title, air date, perex."""
    base_host = urlparse(base_url).netloc.lower()
    stubs: list[ArticleStub] = []
    seen: set[str] = set()
    parts = _CARD_SPLIT_RE.split(html)
    for card in parts[1:]:
        m = _HEADING_RE.search(card)
        if not m:
            continue
        url = urljoin(base_url, m.group(1))
        if urlparse(url).netloc.lower() != base_host or url in seen:
            continue
        if not re.search(r"-\d{7,}$", url):
            continue
        seen.add(url)
        title = unescape(_TAG_RE.sub("", m.group(2))).strip()
        dm = _DATE_RE.search(card)
        pm = _PEREX_RE.search(card)
        stubs.append(ArticleStub(
            url=url,
            title=title,
            published_at=parse_czech_date(dm.group(1)) if dm else None,
            perex=unescape(pm.group(1)).strip() if pm else None,
        ))
    return stubs


def fetch_archive_stubs(url: str, max_pages: int = 60,
                        fetch=None) -> list[ArticleStub]:
    """Walk the paginated archive (?page=N) and collect every episode card.

    Stops at the first page with no NEW stubs or at max_pages. `fetch`
    is injectable for tests (url -> html)."""
    if fetch is None:
        def fetch(u: str) -> str:
            return fetch_station_page(u)[1]

    all_stubs: list[ArticleStub] = []
    seen: set[str] = set()
    for page in range(0, max_pages):
        page_url = url if page == 0 else f"{url}?page={page}"
        try:
            html = fetch(page_url)
        except Exception:
            break
        stubs = [s for s in discover_article_stubs(html, url) if s.url not in seen]
        if not stubs:
            break
        for s in stubs:
            seen.add(s.url)
        all_stubs.extend(stubs)
    return all_stubs


def filter_serial_entries(entries, page_title: str | None):
    """Drop 'related player' entries embedded on a book page.

    Book pages embed the serial's parts (identically titled) PLUS unrelated
    promo players (found live: a Škvorecký jazz feature inside URaNovA, a
    different book downloaded by playlist position). Rule: when one
    normalized title covers ≥ half of the entries (the serial), keep only
    entries matching it or the page title; otherwise keep everything.

    Returns (kept, dropped).
    """
    from collections import Counter
    from unidecode import unidecode

    def norm(t: str | None) -> str:
        return unidecode((t or "").split(".")[0]).lower().strip()

    if len(entries) < 3:
        return list(entries), []
    counts = Counter(norm(getattr(e, "title", None)) for e in entries)
    majority, m_count = counts.most_common(1)[0]
    if not majority or m_count < len(entries) / 2:
        return list(entries), []
    page_n = norm(page_title)
    kept, dropped = [], []
    for e in entries:
        t = norm(getattr(e, "title", None))
        if t == majority or (page_n and (t in page_n or page_n in t)):
            kept.append(e)
        else:
            dropped.append(e)
    return kept, dropped


def fetch_station_page(url: str, timeout: int = 30) -> tuple[str | None, str]:
    """Fetch a station page; return (title, html).

    Title prefers <h1> (program name), falls back to <title> stripped of
    the " | Český rozhlas …" suffix.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "audiobiblio"})
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    title: str | None = None
    m = _TITLE_RE.search(html)
    if m:
        raw = m.group(1) or m.group(2) or ""
        title = unescape(_TAG_RE.sub("", raw)).split("|")[0].strip() or None
    return title, html
