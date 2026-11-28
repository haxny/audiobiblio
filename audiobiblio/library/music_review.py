"""Album-feature music programs (e.g. Kulata muzika): one episode presents
one album, mostly as music samples. This is MUSIC (music share, never the
audiobook library).

Titles look like 'Artist - Album (2017)' or 'Artist: Album (1991)'. The
reviewed artist becomes the artist tag, so Plex/Plexamp pair the episode with
the artist's own albums; the program is the album. Free-text titles are never
guessed: they keep the whole title and get no artist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from unidecode import unidecode

MAX_STEM = 80
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_SEP = re.compile(r"\s+-\s+|:\s+")
_FS_BAD = re.compile(r"[\\/:*?\"<>|]+")


@dataclass(frozen=True)
class ReviewTitle:
    artist: str
    album: str
    year: int | None


def parse_review_title(title: str | None) -> ReviewTitle | None:
    t = (title or "").strip()
    if not t:
        return None
    parts = _SEP.split(t, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    artist, rest = parts[0].strip(), parts[1].strip()
    m = _YEAR.search(rest)
    year = int(m.group(1)) if m else None
    album = _YEAR.sub("", rest).strip()
    if not album:
        return None
    return ReviewTitle(artist, album, year)


# Programs whose episodes are album reviews (matched by crawl-target URL).
REVIEW_PROGRAM_SLUGS = ("kulata-muzika",)


def is_review_program(program) -> bool:
    """True for programs that review one album per episode and are music
    targets (content class MUSIC-*). Anything unexpected -> False."""
    try:
        url = (getattr(program, "url", None) or "").lower()
        if not any(slug in url for slug in REVIEW_PROGRAM_SLUGS):
            return False
        from sqlalchemy.orm import object_session
        from audiobiblio.core.db.models import CrawlTarget
        sess = object_session(program)
        if sess is None:
            return False
        cls = sess.query(CrawlTarget.content_class).filter(
            CrawlTarget.url == program.url).scalar()
        return bool(cls and cls.startswith("MUSIC"))
    except Exception:
        return False


def _u(s: str | None) -> str:
    return unidecode(s or "")


def _air_date(ep) -> str:
    pub = getattr(ep, "published_at", None)
    return pub.strftime("%Y%m%d") if pub else ""


def _label(r: ReviewTitle) -> str:
    return f"{r.album} ({r.year})" if r.year else r.album


def review_stem(ep) -> str:
    """'YYYYMMDD Artist - Album (year)' — air date first, ascii, fs-safe."""
    r = parse_review_title(ep.title)
    body = f"{r.artist} - {_label(r)}" if r else (ep.title or "").strip()
    stem = " ".join(filter(None, (_air_date(ep), _u(body))))
    stem = re.sub(r"\s+", " ", _FS_BAD.sub(" ", stem)).strip()
    return stem[:MAX_STEM].rstrip(". ") or "track"


def review_tags(ep, *, program_name: str, genre: str,
                www: str = "") -> tuple[dict, dict]:
    """(album_tags, track_tags) for write_tags; every value unidecoded."""
    r = parse_review_title(ep.title)
    pub = getattr(ep, "published_at", None)
    artist = _u(r.artist) if r else ""
    album_tags = {
        "album": _u(program_name),
        "artist": artist,
        "albumartist": artist,
        "genre": genre,
        "date": pub.strftime("%Y-%m-%d") if pub else "",
        "www": www,
    }
    track_tags = {
        "title": _u(_label(r) if r else (ep.title or "").strip()),
        "tracknumber": "",
    }
    return album_tags, track_tags
