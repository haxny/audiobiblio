"""Extend an old description with article paragraphs it is missing.

Until 2026-11-09 the scraper kept only the first paragraph of the article
body. Repair rule: the old description must contain the first body
paragraph verbatim; the missing ones are inserted right after it, so the
header, cast/crew lines and URLs keep their places. Anything else (a
rewritten or hand-edited description) is left alone.
"""
from __future__ import annotations


def extend_with_missing_body(old: str | None, body: list[str]) -> str | None:
    """New description, or None when there is nothing safe to add."""
    if not old or len(body) < 2 or body[0] not in old:
        return None
    missing = [p for p in body[1:] if p not in old]
    if not missing:
        return None
    anchor = old.index(body[0]) + len(body[0])
    return old[:anchor] + "\n\n" + "\n\n".join(missing) + old[anchor:]
