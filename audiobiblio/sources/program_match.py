"""Find the real program for an item that arrived without one.

Ingest paths that call upsert_from_item without a program name (the
yt-dlp crawler, manual URL ingest, CLI) used to drop the item into the
catch-all program "mujrozhlas" and name the work after the show. By
2026-11-11 that program held 845 works / 10,404 episodes, and nine
Radioseriál books (Otec Goriot, Velký Gatsby…) shared one work, so none of
them could be shelved.

The show slug of a mujrozhlas URL (mujrozhlas.cz/<show>/<book>) names the
show; crawl targets and program urls/names map it to the program. Inside a
book program every book gets its own work, keyed by author + title.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from unidecode import unidecode

from audiobiblio.core.db.models import CrawlTarget, Program, Station

CATCH_ALL_PROGRAMS = frozenset({"mujrozhlas"})
_SHOW_HOST = "mujrozhlas.cz"
# first path segments that are not shows
_NOT_SHOWS = frozenset({"rapi", "stream", "audio", "hledat", "vysilani", "podcasty", "stanice"})


def norm_name(name: str | None) -> str:
    """'Četba na pokračování (CRo2) — stanice' → 'cetba na pokracovani'."""
    n = unidecode(name or "").lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.split(r"\s[-—–]+\s", n)[0]
    return " ".join(re.sub(r"[^a-z0-9]+", " ", n).split())


def host(url: str | None) -> str:
    return (urlparse(url or "").netloc or "").lower().removeprefix("www.")


def _segments(url: str | None) -> list[str]:
    if host(url) != _SHOW_HOST:
        return []
    return [p for p in urlparse(url or "").path.split("/") if p]


def show_url(url: str | None) -> str | None:
    """'https://www.mujrozhlas.cz/radioserial/otec-goriot' → '…/radioserial'."""
    seg = _segments(url)
    if not seg or seg[0] in _NOT_SHOWS:
        return None
    return f"https://www.mujrozhlas.cz/{seg[0]}"


def book_url(url: str | None) -> str | None:
    """The book page part of a mujrozhlas URL, when there is one."""
    seg = _segments(url)
    if len(seg) < 2 or seg[0] in _NOT_SHOWS:
        return None
    return f"https://www.mujrozhlas.cz/{seg[0]}/{seg[1]}"


def is_catch_all(program: Program | None) -> bool:
    return program is not None and norm_name(program.name) in CATCH_ALL_PROGRAMS


def best_program(cands: list, target_host: str):
    """Among twin programs: the one on the target's station wins, a
    'mujrozhlas' twin loses, then the oldest. Works for ORM rows and for
    SQL rows with .id/.code/.website."""
    def rank(p):
        code = getattr(p, "code", None) or (p.station.code if getattr(p, "station", None) else "")
        website = getattr(p, "website", None) or (p.station.website if getattr(p, "station", None) else "")
        return (host(website) == target_host, (code or "").lower() != "mujrozhlas", -p.id)
    return max(cands, key=rank) if cands else None


def resolve_program(session: Session, url: str | None,
                    names: tuple[str | None, ...] = ()) -> Program | None:
    """Real program for an item from show `url` or show `names`.

    Evidence: a crawl target whose url/paired url is the show page (its
    host decides among twins), program urls, then unaccented names. Never
    returns the catch-all program."""
    show = show_url(url)
    urls: set[str] = set()
    target_host = ""
    want = {norm_name(n) for n in names if n}
    if show:
        urls.add(show)
        for t in session.query(CrawlTarget).filter(
                (CrawlTarget.url == show) | (CrawlTarget.paired_url == show)).all():
            urls.update(u.rstrip("/") for u in (t.url, t.paired_url) if u)
            want.add(norm_name(t.name))
            if not target_host or host(t.url) != _SHOW_HOST:
                target_host = host(t.url)
    want.discard("")
    if not urls and not want:
        return None
    cands = []
    for p in session.query(Program).join(Station).all():
        if is_catch_all(p):
            continue
        if (p.url and p.url.rstrip("/") in urls) or norm_name(p.name) in want:
            cands.append(p)
    return best_program(cands, target_host)
