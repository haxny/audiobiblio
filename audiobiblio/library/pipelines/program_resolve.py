"""Book-level identity inside a resolved program (see sources.program_match).

In a book program every book gets its own work, keyed by author + title —
the yt-dlp path used to name the work after the show and glue nine
Radioseriál books into one (live 2026-11-11).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Program, Series, Work
from audiobiblio.sources.program_match import (  # noqa: F401  (re-exported)
    CATCH_ALL_PROGRAMS, best_program, book_url, host, is_catch_all, norm_name,
    resolve_program, show_url,
)


def book_key(raw_title: str | None) -> tuple[str, str] | None:
    """(author, title) identity of a book heading, both unaccented lower;
    author may be ''."""
    from audiobiblio.library.book_meta import parse_book_title
    meta = parse_book_title(raw_title or "")
    title = norm_name(meta.title)
    if not title:
        return None
    return norm_name(meta.author), title


def find_book_work(session: Session, program: Program, raw_title: str | None) -> Work | None:
    """Existing work of `program` that is the same book as `raw_title`
    (title equal; authors equal when both are known)."""
    key = book_key(raw_title)
    if key is None:
        return None
    author, title = key
    works = (session.query(Work).join(Series)
             .filter(Series.program_id == program.id).all())
    for w in works:
        wk = book_key(w.title)
        if wk and wk[1] == title and _same_author(wk[0], author):
            return w
    return None


def _same_author(a: str, b: str) -> bool:
    """Unknown on either side, or the same surname ('f s fitzgerald' vs
    'francis scott fitzgerald' — station page vs episode title)."""
    return not a or not b or a.split()[-1] == b.split()[-1]
