"""List episodes whose url is an audio stream instead of an episode page.

READ-ONLY: prints a TSV report, never writes to the DB. Repair later by
moving the stream url to an EpisodeAlias and setting Episode.url to the page
(the page_alias column shows the page we already know, if any; show/serial
sweep rows can use https://www.mujrozhlas.cz/rapi/view/episode/<ext_id>).

Usage:
    python scripts/find_stream_urls.py            # TSV to stdout
    python scripts/find_stream_urls.py --summary  # counts per discovery_source
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Episode, EpisodeAlias
from audiobiblio.core.urls import is_media_url, page_url

# SQL prefilter only (cheap); is_media_url() is the authoritative check.
_STREAM_PATTERNS = ("%croaod.cz/stream%", "%.m3u8%")


@dataclass(frozen=True)
class StreamUrlRow:
    episode_id: int
    work_id: int
    ext_id: str | None
    discovery_source: str | None
    url: str
    page_alias: str | None


def find_stream_episodes(session: Session) -> list[StreamUrlRow]:
    """Episodes whose url is a stream (croaod.cz/stream or .m3u8)."""
    eps = (session.query(Episode)
           .filter(or_(*(Episode.url.like(p) for p in _STREAM_PATTERNS)))
           .order_by(Episode.id)
           .all())
    rows: list[StreamUrlRow] = []
    for ep in eps:
        if not is_media_url(ep.url):
            continue
        aliases = [a.url for a in session.query(EpisodeAlias.url)
                   .filter(EpisodeAlias.episode_id == ep.id)
                   .order_by(EpisodeAlias.id)]
        rows.append(StreamUrlRow(
            episode_id=ep.id, work_id=ep.work_id, ext_id=ep.ext_id,
            discovery_source=ep.discovery_source, url=ep.url,
            page_alias=page_url(None, aliases)))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", action="store_true",
                    help="print counts per discovery_source only")
    args = ap.parse_args(argv)

    from audiobiblio.core.db.session import get_session
    session = get_session()
    try:
        rows = find_stream_episodes(session)
    finally:
        session.close()

    if args.summary:
        counts = Counter(r.discovery_source or "-" for r in rows)
        for source, n in counts.most_common():
            sys.stdout.write(f"{n}\t{source}\n")
        sys.stdout.write(f"{len(rows)}\tTOTAL\n")
        return 0
    sys.stdout.write("episode_id\twork_id\text_id\tdiscovery_source\turl\tpage_alias\n")
    for r in rows:
        sys.stdout.write("\t".join(str(v) if v is not None else "" for v in (
            r.episode_id, r.work_id, r.ext_id, r.discovery_source, r.url,
            r.page_alias)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
