"""
completeness — Work-level download completeness against expected totals.

Decision: new module, not an extension of gaps.py.
  gaps.py handles CatalogEntry-based availability reconciliation (program-level,
  comparing scraped catalog entries against files on disk).
  This module handles Work-level completeness: counted COMPLETE audio assets
  against a manually set expected_total.  Different models, different domain,
  single-responsibility principle.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import String, case, cast, func
from sqlalchemy.orm import Session, joinedload

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, Series, Work
from audiobiblio.library.pipelines.shared_parts import shared_episodes, shared_part_ids

log = structlog.get_logger()

# Minimum fraction of episodes that must have distinct positive episode_number
# for the numbering to be considered trustworthy enough to compute missing_numbers.
_NUMBERING_THRESHOLD = 0.80


def _part_key():
    """SQL expression identifying one PART of a work.

    Numbered episodes are one part per number: two records of part 3 (found
    by two discovery paths) are still part 3. Unnumbered episodes count
    individually. Counting records instead of parts marked a 5-part serial
    "complete" with part 2 missing (parts 3-5 recorded twice).
    """
    return case(
        (Episode.episode_number > 0, cast(Episode.episode_number, String)),
        else_="id:" + cast(Episode.id, String),
    )


def _audio_parts_subquery(session: Session):
    """work_id -> number of distinct parts with a COMPLETE audio asset."""
    return (
        session.query(
            Episode.work_id.label("work_id"),
            func.count(_part_key().distinct()).label("have"),
        )
        .join(Asset, Asset.episode_id == Episode.id)
        .filter(Asset.type == AssetType.AUDIO, Asset.status == AssetStatus.COMPLETE)
        .group_by(Episode.work_id)
        .subquery()
    )


def complete_audio_count(session: Session, work_id: int) -> int:
    """Return the count of distinct PARTS in *work_id* with a COMPLETE AUDIO asset
    (see _part_key: duplicate records of one numbered part count once).

    Single source of truth for the "have" count — shared by work_completeness,
    checks._work_has_gap, and ingest._apply_gap_fill_priority. Parts the work
    borrows from another work (shared_parts: a re-aired recording) count too.
    """
    own_keys = {
        key for (key,) in session.query(_part_key())
        .join(Asset, Asset.episode_id == Episode.id)
        .filter(
            Episode.work_id == work_id,
            Asset.type == AssetType.AUDIO,
            Asset.status == AssetStatus.COMPLETE,
        )
        .distinct()
    }
    # A borrowed part counts under its number in THIS work (part 2 of the
    # serial), not its number in the home work (often 1).
    shared = shared_part_ids(session.get(Work, work_id))
    if shared:
        with_audio = {
            ep_id for (ep_id,) in session.query(Asset.episode_id).filter(
                Asset.episode_id.in_(list(shared.values())),
                Asset.type == AssetType.AUDIO,
                Asset.status == AssetStatus.COMPLETE,
            )
        }
        own_keys |= {str(part) for part, ep_id in shared.items() if ep_id in with_audio}
    return len(own_keys)


@dataclass(frozen=True)
class Completeness:
    """Completeness snapshot for one Work.

    Attributes:
        have: Count of episodes in this work that have at least one
              COMPLETE AUDIO asset.
        expected: The work's expected_total (None if not set).
        missing_numbers: Sorted list of episode numbers from 1..expected that
                         have no COMPLETE audio — computed only when numbering
                         is trustworthy (see heuristic below).  None otherwise.

    Numbering trustworthiness heuristic
    ------------------------------------
    A work's episode numbering is *trustworthy* when:

        count_distinct_positive_episode_numbers
        ----------------------------------------  >= 0.80
             total_episodes_in_work

    where "distinct positive" means episode_number is not None and > 0.

    Rationale: many feeds assign episode_number inconsistently (all-None,
    duplicate numbers, or 0-based counts).  If fewer than 80 % of episodes
    carry a distinct, positive number, the resulting "missing" list would be
    misleading.  In that case missing_numbers is None, not an empty list.
    When expected is None, missing_numbers is always None.
    """

    have: int
    expected: int | None
    missing_numbers: list[int] | None


def work_completeness(session: Session, work: Work) -> Completeness:
    """Compute completeness for *work*.

    'have' is the count of episodes that have a COMPLETE AUDIO asset.
    The episode and asset rows are queried; the work.episodes ORM relationship
    is NOT relied on to avoid accidental lazy-loading in async contexts.
    """
    # Fetch all episodes for this work, plus parts borrowed from other works
    # (numbered by their part in THIS work, not in their home work)
    own = (
        session.query(Episode)
        .filter(Episode.work_id == work.id)
        .all()
    )
    part_of = {ep_id: part for part, ep_id in shared_part_ids(work).items()}
    borrowed = [e for e in shared_episodes(session, work) if e.work_id != work.id]
    numbers = {e.id: e.episode_number for e in own}
    numbers.update({e.id: part_of[e.id] for e in borrowed})
    episodes = own + borrowed
    total = len(episodes)

    if total == 0:
        return Completeness(
            have=0,
            expected=work.expected_total,
            missing_numbers=None,
        )

    # Which episode IDs have a COMPLETE audio asset?
    ep_ids = [e.id for e in episodes]
    complete_ids: set[int] = set(
        row[0]
        for row in session.query(Episode.id)
        .join(Asset, Asset.episode_id == Episode.id)
        .filter(
            Episode.id.in_(ep_ids),
            Asset.type == AssetType.AUDIO,
            Asset.status == AssetStatus.COMPLETE,
        )
        .distinct()
        .all()
    )
    # Same rule as complete_audio_count / _part_key: one numbered part counts
    # once however many records it has; unnumbered episodes count individually.
    # Borrowed parts use their number in THIS work (`numbers`).
    def part(e: Episode):
        n = numbers[e.id]
        return n if n is not None and n > 0 else f"id:{e.id}"

    have = len({part(e) for e in episodes if e.id in complete_ids})

    # Numbering trustworthiness — over distinct parts, so duplicate records
    # of a part do not make good numbering look unreliable.
    distinct_positive = {
        numbers[e.id]
        for e in episodes
        if numbers[e.id] is not None and numbers[e.id] > 0
    }
    parts_total = len({part(e) for e in episodes})
    trustworthy = len(distinct_positive) >= _NUMBERING_THRESHOLD * parts_total

    # Compute missing_numbers when trustworthy and expected_total is known
    if trustworthy and work.expected_total is not None:
        # Numbers we already have COMPLETE audio for
        numbers_with_audio: set[int] = {
            numbers[e.id]
            for e in episodes
            if e.id in complete_ids
            and numbers[e.id] is not None
            and numbers[e.id] > 0
        }
        all_expected = set(range(1, work.expected_total + 1))
        missing_numbers: list[int] | None = sorted(all_expected - numbers_with_audio)
    else:
        missing_numbers = None

    return Completeness(
        have=have,
        expected=work.expected_total,
        missing_numbers=missing_numbers,
    )


def incomplete_works(session: Session, limit: int = 100) -> list[tuple[Work, int]]:
    """Return (work, have) pairs where work has expected_total set and have < expected_total.

    Sorted by (expected_total - have) ascending — the most nearly complete works
    (smallest remaining gap) come first, giving the best prioritisation signal.

    'have' is the count of distinct episodes in the work that have a COMPLETE
    AUDIO asset.  Works with expected_total=None or have>=expected_total are excluded.

    Series and Program are eager-loaded to avoid N+1 queries in views._query_gaps.
    """
    audio_sub = _audio_parts_subquery(session)

    have_col = func.coalesce(audio_sub.c.have, 0)
    gap_col = Work.expected_total - have_col

    rows = (
        session.query(Work, have_col.label("have"))
        .options(
            joinedload(Work.series).joinedload(Series.program)
        )
        .outerjoin(audio_sub, audio_sub.c.work_id == Work.id)
        .filter(Work.expected_total.isnot(None))
        .filter(have_col < Work.expected_total)
        .order_by(gap_col.asc())
        .limit(limit)
        .all()
    )

    return [(work, int(have)) for work, have in rows]


def completed_works(session: Session, limit: int = 100) -> list[tuple[Work, int]]:
    """Return (work, have) pairs where expected_total is set and have >= expected_total.

    The mirror of incomplete_works — these are the works eligible for
    finalization into a per-work folder (Phase 5 Task 7).  Sorted by title
    for a stable, human-friendly listing.

    Series and Program are eager-loaded for the /gaps view (same pattern as
    incomplete_works).
    """
    audio_sub = _audio_parts_subquery(session)

    have_col = func.coalesce(audio_sub.c.have, 0)

    rows = (
        session.query(Work, have_col.label("have"))
        .options(
            joinedload(Work.series).joinedload(Series.program)
        )
        .outerjoin(audio_sub, audio_sub.c.work_id == Work.id)
        .filter(Work.expected_total.isnot(None))
        .filter(have_col >= Work.expected_total)
        .order_by(Work.title.asc())
        .limit(limit)
        .all()
    )

    return [(work, int(have)) for work, have in rows]


def count_incomplete_works(session: Session) -> int:
    """Return the count of works that have expected_total set and have < expected_total.

    Used for the console badge — keeps the query lightweight (no per-work asset loads).
    """
    audio_sub = _audio_parts_subquery(session)

    have_col = func.coalesce(audio_sub.c.have, 0)

    return (
        session.query(func.count(Work.id))
        .outerjoin(audio_sub, audio_sub.c.work_id == Work.id)
        .filter(Work.expected_total.isnot(None))
        .filter(have_col < Work.expected_total)
        .scalar()
    ) or 0
