"""shared_parts — one recording that is a numbered part of a second work.

A part can air first on its own (Radiokniha one-shot) and later re-air as
part N of a serial (live: 'Miloš Doležal: Bohouš svině', ext 12151052, part 2
of 'Dokumentární povídky…'). Episode.ext_id is unique and Episode.work_id is
a single FK, so the serial can never own that episode — it showed a gap.

Storage, no migration: the borrowing work records the link in its JSON
``Work.extra["shared_parts"] = {"<part number>": <episode id>}``. The episode
stays in its first work (one download, one file); completeness counts it for
both works and finalize places a link/copy of its file into the borrower's
folder without moving the original.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Episode, Work

log = structlog.get_logger()

EXTRA_KEY = "shared_parts"


def _norm_title(t: str | None) -> str:
    from unidecode import unidecode
    return " ".join(unidecode(t or "").lower().split())


def shared_part_ids(work: Work | None) -> dict[int, int]:
    """{part number: episode id} borrowed by *work* from other works."""
    raw = ((work.extra or {}) if work is not None else {}).get(EXTRA_KEY) or {}
    out: dict[int, int] = {}
    for part, ep_id in raw.items():
        try:
            out[int(part)] = int(ep_id)
        except (TypeError, ValueError):
            log.warning("shared_part_bad_entry", work_id=work.id, part=part, ep=ep_id)
    return out


def shared_episodes(session: Session, work: Work) -> list[Episode]:
    """The borrowed episodes, ordered by their part number in *work*."""
    ids = shared_part_ids(work)
    if not ids:
        return []
    by_id = {e.id: e for e in session.query(Episode).filter(Episode.id.in_(ids.values()))}
    return [by_id[i] for _, i in sorted(ids.items()) if i in by_id]


def link_shared_part(session: Session, episode: Episode, work: Work, part: int) -> bool:
    """Record *episode* as part *part* of *work* (a different work than its
    own). Conservative — returns False and changes nothing unless:

    * the work already has OWN episodes (a container whose every part is
      already known elsewhere is a re-listing of the same book, not a new
      serial — that case drops the empty shell instead),
    * the titles differ (same title = same book re-listed),
    * no own episode of *work* already carries that part number.
    """
    if not part or part <= 0 or episode.work_id == work.id:
        return False
    if episode.work is not None and _norm_title(episode.work.title) == _norm_title(work.title):
        return False
    own = session.query(Episode.id, Episode.episode_number).filter(
        Episode.work_id == work.id).all()
    if not own or any(num == part for _, num in own):
        return False
    current = shared_part_ids(work)
    if current.get(part) == episode.id:
        return False
    if part in current:
        log.warning("shared_part_conflict", work_id=work.id, part=part,
                    have=current[part], incoming=episode.id)
        return False
    links = {str(k): v for k, v in current.items()}
    work.extra = {**(work.extra or {}), EXTRA_KEY: {**links, str(part): episode.id}}
    log.info("shared_part_linked", work_id=work.id, part=part, episode_id=episode.id,
             home_work_id=episode.work_id)
    return True
