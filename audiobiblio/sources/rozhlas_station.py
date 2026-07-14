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
