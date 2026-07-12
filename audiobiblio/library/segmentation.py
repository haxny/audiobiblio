"""
segmentation — Propose segmentation of a program's episodes into ProposedWork objects.

Pure analysis: NO writes, NO file reads, NO network calls.

Algorithm
---------
1. Collect all episodes via program → series_list → works → episodes.
2. Skip generic/fallback titles (is_generic_title or ^Episode \\d+$) → unassigned.
3. Parse each episode title with the author-prefix pattern.
4. Strip part-markers from rest to derive book_key.
5. Assign signal per episode: "author_title_parts", "author_title", or "episode_title".
6. Determine mode = majority signal across non-unassigned episodes.
7. Cluster serialized episodes by (author, book_key); anthology/magazine per-episode.
8. Return SegmentationProposal with proposed + unassigned tuples.

Future signal (not implemented): a META_JSON-derived ``series`` MetadataValue that
differs from the program name could be preferred as book_key — series is not
recorded by the ingest today, and this engine must not read files, so it is
deliberately skipped.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from audiobiblio.dedupe.matching import is_generic_title
from audiobiblio.tags.diacritics import _CZECH_PARTS  # shared Czech ordinals, not duplicated

# ---------------------------------------------------------------------------
# Author-prefix regex (brief-specified, Czech uppercase letters in class)
# ---------------------------------------------------------------------------
_AUTHOR_PREFIX_RE = re.compile(
    r"^([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^:]{2,60}?):\s+(.+)$"
)

# Guard: author candidate must look like a name (≤4 words, no digits)
_HAS_DIGIT_RE = re.compile(r"\d")

# Generic episode fallback pattern
_EPISODE_N_RE = re.compile(r"^Episode \d+$")

# Part-marker patterns ordered most-specific first.
# Each captures the numeric part (if any) for ordering.
_PART_PATTERNS: list[tuple[re.Pattern[str], re.Pattern[str] | None]] = [
    # (N/M) or N/M at end — e.g. "(1/2)", "2/5"
    (re.compile(r"\s*\(?\s*(\d+)\s*/\s*\d+\s*\)?\s*$"), re.compile(r"(\d+)")),
    # N. díl / N. část (with optional parens) — e.g. "1. díl", "(2. část)"
    (
        re.compile(r"\s*\(?\s*(\d+)\.\s*(?:díl|část)\s*\)?\s*$", re.IGNORECASE),
        re.compile(r"(\d+)"),
    ),
    # ", část <word>" — e.g. ", část první"  (no numeric part number)
    (re.compile(r"\s*,?\s*část\s+\w+\s*$", re.IGNORECASE), None),
    # " - N" at end — e.g. "Kolumbus - 1"
    (re.compile(r"\s*-\s*(\d+)\s*$"), re.compile(r"(\d+)")),
]


def _looks_like_name(s: str) -> bool:
    """Return True if s could be a person's name: ≤4 words, no digits."""
    stripped = s.strip()
    if not stripped:
        return False
    if _HAS_DIGIT_RE.search(stripped):
        return False
    words = stripped.split()
    return 1 <= len(words) <= 4


def _ascii_lower(s: str) -> str:
    """Strip diacritics and lowercase — same normalisation as _CZECH_PARTS keys."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _strip_part_marker(rest: str) -> tuple[str, bool, int]:
    """Strip trailing part-marker from rest.

    Checks regex patterns first, then falls back to _CZECH_PARTS ordinal lookup
    (e.g. ", část první" → key ", cast prvni" in _CZECH_PARTS).

    Returns (book_key, has_marker, part_num).
    part_num is 0 if no numeric part could be extracted.
    """
    # --- Regex-based patterns ---
    for pat, num_pat in _PART_PATTERNS:
        m = pat.search(rest)
        if m:
            book_key = rest[: m.start()].strip()
            part_num = 0
            if num_pat:
                nm = num_pat.search(m.group(0))
                if nm:
                    part_num = int(nm.group(1))
            return book_key, True, part_num

    # --- _CZECH_PARTS ordinal lookup (e.g. ", část první") ---
    rest_norm = _ascii_lower(rest)
    for key, suffix in _CZECH_PARTS.items():
        if rest_norm.endswith(key):
            # Extract part number from suffix string like "-01", "-02"
            book_key = rest[: len(rest) - len(key)].strip()
            try:
                part_num = int(suffix.lstrip("-"))
            except ValueError:
                part_num = 0
            return book_key, True, part_num

    return rest, False, 0


def _parse_episode_title(title: str) -> tuple[str | None, str, bool, int]:
    """Parse episode title.

    Returns (author | None, rest_or_title, has_part_marker, part_num).
    """
    m = _AUTHOR_PREFIX_RE.match(title)
    if m:
        author_candidate = m.group(1).strip()
        rest = m.group(2).strip()
        if _looks_like_name(author_candidate):
            book_key, has_part, part_num = _strip_part_marker(rest)
            return author_candidate, book_key if has_part else rest, has_part, part_num
    return None, title, False, 0


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedWork:
    """One proposed work derived from episode-title analysis.

    Fields
    ------
    title       : book/story title (part-markers stripped for serialized)
    author      : detected author or None (magazine)
    episode_ids : ordered tuple of episode DB IDs belonging to this work
    signal      : "author_title_parts" | "author_title" | "episode_title"
    confidence  : 1.0 (serialized cluster) | 0.9 (anthology) | 0.7 (magazine)
    """

    title: str
    author: str | None
    episode_ids: tuple[int, ...]
    signal: str
    confidence: float


@dataclass(frozen=True)
class SegmentationProposal:
    """Analysis result for one program.

    Fields
    ------
    program_id  : FK to programs table
    mode        : majority signal mode ("serialized"|"anthology"|"magazine")
    proposed    : tuple of ProposedWork (ordered: serialized clusters first, then rest)
    unassigned  : tuple of episode IDs with generic/fallback titles
    note        : human-readable explanation
    """

    program_id: int
    mode: str
    proposed: tuple[ProposedWork, ...]
    unassigned: tuple[int, ...]
    note: str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_SIGNAL_TO_MODE = {
    "author_title_parts": "serialized",
    "author_title": "anthology",
    "episode_title": "magazine",
}


def propose_segmentation(session, program) -> SegmentationProposal:
    """Analyse *program*'s episodes and return a SegmentationProposal.

    Pure read-only: no writes, no file I/O, no network.

    Parameters
    ----------
    session : SQLAlchemy session (used only for lazy-load access)
    program : Program ORM object (must be attached to *session*)
    """
    # ------------------------------------------------------------------
    # 1. Collect all episodes
    # ------------------------------------------------------------------
    all_episodes: list = []
    for series in program.series_list:
        for work in series.works:
            for ep in work.episodes:
                all_episodes.append(ep)

    if not all_episodes:
        return SegmentationProposal(
            program_id=program.id,
            mode="magazine",
            proposed=(),
            unassigned=(),
            note="No episodes found.",
        )

    # ------------------------------------------------------------------
    # 2. Classify each episode
    # ------------------------------------------------------------------
    unassigned_ids: list[int] = []

    # For serialized clustering: {(author, book_key): [(ep_id, part_num, published_at), ...]}
    serialized_clusters: dict[tuple[str, str], list[tuple[int, int, object]]] = (
        defaultdict(list)
    )

    # Author-prefixed titles WITHOUT part markers, grouped by (author, title):
    # {(author, rest): [(ep_id, episode_number, published_at), ...]}
    author_title_groups: dict[tuple[str, str], list[tuple[int, int, object]]] = (
        defaultdict(list)
    )

    # Per-episode proposed works (magazine built here)
    per_episode_works: list[ProposedWork] = []

    signal_counts: Counter[str] = Counter()

    for ep in all_episodes:
        title: str = ep.title or ""

        # Generic / fallback guard
        if is_generic_title(title) or _EPISODE_N_RE.match(title):
            unassigned_ids.append(ep.id)
            continue

        author, rest, has_part, part_num = _parse_episode_title(title)

        if author is not None and has_part:
            signal = "author_title_parts"
            signal_counts[signal] += 1
            serialized_clusters[(author, rest)].append(
                (ep.id, part_num, ep.published_at)
            )

        elif author is not None:
            signal = "author_title"
            signal_counts[signal] += 1
            # Collect instead of emitting per-episode: mujrozhlas embeds all
            # parts of a book with IDENTICAL titles (no part markers) — the
            # same (author, title) appearing on several episodes is one book,
            # not N anthology stories. Grouped after the loop.
            author_title_groups[(author, rest)].append(
                (ep.id, ep.episode_number or 0, ep.published_at)
            )

        else:
            signal = "episode_title"
            signal_counts[signal] += 1
            per_episode_works.append(
                ProposedWork(
                    title=title,
                    author=None,
                    episode_ids=(ep.id,),
                    signal=signal,
                    confidence=0.7,
                )
            )

    # ------------------------------------------------------------------
    # 2b. Emit author-title groups: identical (author, title) on multiple
    #     episodes = ONE serialized book (parts ordered by episode_number);
    #     a singleton stays a per-episode anthology story.
    # ------------------------------------------------------------------
    for (author, rest), entries in author_title_groups.items():
        sorted_entries = sorted(
            entries,
            key=lambda e: (e[1] if e[1] > 0 else 9999, str(e[2]) if e[2] else ""),
        )
        per_episode_works.append(
            ProposedWork(
                title=rest,
                author=author,
                episode_ids=tuple(e[0] for e in sorted_entries),
                signal="author_title",
                confidence=0.9,
            )
        )

    # ------------------------------------------------------------------
    # 3. Determine mode (majority signal)
    # ------------------------------------------------------------------
    if not signal_counts:
        mode = "magazine"
    else:
        dominant_signal = signal_counts.most_common(1)[0][0]
        mode = _SIGNAL_TO_MODE[dominant_signal]

    # ------------------------------------------------------------------
    # 4. Build ProposedWorks for serialized clusters
    # ------------------------------------------------------------------
    serialized_works: list[ProposedWork] = []
    for (author, book_key), entries in serialized_clusters.items():
        # Order by part_num then published_at
        sorted_entries = sorted(
            entries,
            key=lambda e: (e[1] if e[1] > 0 else 9999, str(e[2]) if e[2] else ""),
        )
        ep_ids = tuple(e[0] for e in sorted_entries)
        confidence = 1.0 if len(ep_ids) > 1 else 0.9
        serialized_works.append(
            ProposedWork(
                title=book_key,
                author=author,
                episode_ids=ep_ids,
                signal="author_title_parts",
                confidence=confidence,
            )
        )

    # ------------------------------------------------------------------
    # 5. Assemble result
    # ------------------------------------------------------------------
    proposed = tuple(serialized_works) + tuple(per_episode_works)

    note_parts: list[str] = []
    if serialized_works:
        note_parts.append(f"{len(serialized_works)} serialized work(s)")
    if per_episode_works:
        anthem_count = sum(1 for pw in per_episode_works if pw.signal == "author_title")
        mag_count = sum(
            1 for pw in per_episode_works if pw.signal == "episode_title"
        )
        if anthem_count:
            note_parts.append(f"{anthem_count} anthology story(ies)")
        if mag_count:
            note_parts.append(f"{mag_count} magazine episode(s)")
    if unassigned_ids:
        note_parts.append(f"{len(unassigned_ids)} unassigned (generic/fallback)")
    note = "; ".join(note_parts) if note_parts else "No classifiable episodes."

    return SegmentationProposal(
        program_id=program.id,
        mode=mode,
        proposed=proposed,
        unassigned=tuple(unassigned_ids),
        note=note,
    )


# ---------------------------------------------------------------------------
# apply_segmentation — Task 2
# ---------------------------------------------------------------------------


def apply_segmentation(
    session,
    proposal: SegmentationProposal,
    dry_run: bool = True,
    only_titles: set[str] | None = None,
) -> list[str]:
    """Apply a SegmentationProposal by finding or creating Works and re-parenting episodes.

    Series-ID grouping
    ------------------
    Each ProposedWork's episodes are grouped by their CURRENT series_id
    (``episode.work.series_id``).  Episodes from the same proposed work may live
    under different Series when the program has multiple series — a separate Work
    is found-or-created for each (series_id, title) pair.  In the common case
    every episode shares one series_id, producing exactly one Work per
    ProposedWork.

    Provenance rules
    ----------------
    * Old work's ``expected_total`` MANUAL rows are NOT transferred.  When
      encountered (old work becomes empty), the action list notes
      "expected_total X left on old work — review".
    * When a ProposedWork carries an author, a SCRAPED MetadataValue with
      source="segmentation" is recorded on the new work via ``record_value``.

    Cleanup of old catch-all works
    ------------------------------
    After re-parenting, an old work left with 0 episodes is deleted if and
    only if it has *no* MANUAL ``metadata_values`` rows.  Works that still hold
    episodes are never touched.

    Dry-run purity
    --------------
    When ``dry_run=True``, **no session mutations** (add / delete / flush /
    commit) occur.  The same action list is computed from queries only.

    Commit policy
    -------------
    Real runs: ``session.flush()`` after each new/updated Work, then
    ``session.commit()`` at the end.

    Parameters
    ----------
    session:
        SQLAlchemy session.
    proposal:
        ``SegmentationProposal`` from ``propose_segmentation()``.
    dry_run:
        If True (default), no mutations — query-only.
    only_titles:
        When given, skip ProposedWorks whose title is not in this set.

    Returns
    -------
    list[str]
        Human-readable action strings describing what was (or would be) done.
    """
    from collections import defaultdict as _defaultdict

    from audiobiblio.core.db.models import (
        Episode as _Episode,
        FieldOrigin as _FieldOrigin,
        MetadataValue as _MetadataValue,
        Work as _Work,
    )
    from audiobiblio.core.provenance import record_value as _record_value

    actions: list[str] = []

    # episode_id → old_work_id for episodes that would be (or are) re-parented.
    # Used to simulate cleanup in dry_run mode.
    reparented_from: dict[int, set[int]] = _defaultdict(set)  # old_work_id → ep_ids

    for pw in proposal.proposed:
        if only_titles is not None and pw.title not in only_titles:
            continue

        # Load all episodes for this ProposedWork
        episodes = (
            session.query(_Episode)
            .filter(_Episode.id.in_(pw.episode_ids))
            .all()
        )

        if not episodes:
            actions.append(f"skip: no episodes found for proposed work '{pw.title}'")
            continue

        # Group by the episode's current series_id
        series_groups: dict[int, list] = _defaultdict(list)
        for ep in episodes:
            series_groups[ep.work.series_id].append(ep)

        for series_id, eps_in_series in series_groups.items():
            # Find existing Work matching (series_id, title)
            existing = (
                session.query(_Work)
                .filter_by(series_id=series_id, title=pw.title)
                .first()
            )

            if existing is not None:
                target_id: int | None = existing.id
                # Already all on target?
                if all(ep.work_id == target_id for ep in eps_in_series):
                    actions.append(
                        f"already: {len(eps_in_series)} episode(s) for '{pw.title}'"
                        f" (series {series_id}) are on work #{target_id}"
                    )
                    if pw.author and not dry_run:
                        _record_value(
                            session, "work", target_id, "author",
                            pw.author, _FieldOrigin.SCRAPED, "segmentation",
                        )
                    continue
                to_move_count = sum(
                    1 for ep in eps_in_series if ep.work_id != target_id
                )
                actions.append(
                    f"reparent: {to_move_count} episode(s) → existing work"
                    f" #{target_id} '{pw.title}' (series {series_id})"
                )
            else:
                if not dry_run:
                    new_work = _Work(
                        series_id=series_id,
                        title=pw.title,
                        author=pw.author,
                    )
                    session.add(new_work)
                    session.flush()
                    target_id = new_work.id
                    actions.append(
                        f"create: work #{target_id} '{pw.title}' (series {series_id})"
                    )
                else:
                    target_id = None  # Not created in dry_run
                    actions.append(
                        f"create: work '{pw.title}' (series {series_id})"
                    )

            # Re-parent episodes (or track for dry_run simulation)
            for ep in eps_in_series:
                if ep.work_id != target_id:
                    reparented_from[ep.work_id].add(ep.id)
                    if not dry_run:
                        ep.work_id = target_id

            if not dry_run:
                session.flush()
                if pw.author and target_id is not None:
                    _record_value(
                        session, "work", target_id, "author",
                        pw.author, _FieldOrigin.SCRAPED, "segmentation",
                    )

    # --- Cleanup: check old works that lost (or would lose) all their episodes ---
    for old_work_id, moved_ep_ids in reparented_from.items():
        old_work = session.get(_Work, old_work_id)
        if old_work is None:
            continue

        if dry_run:
            # Simulate remaining count without actually flushing
            total_eps = (
                session.query(_Episode)
                .filter_by(work_id=old_work_id)
                .count()
            )
            remaining = total_eps - len(moved_ep_ids)
        else:
            remaining = (
                session.query(_Episode)
                .filter_by(work_id=old_work_id)
                .count()
            )

        if remaining > 0:
            continue  # Still holds episodes — never delete

        # Emit note for expected_total MANUAL (must not transfer)
        et_manual = (
            session.query(_MetadataValue)
            .filter_by(
                entity_type="work",
                entity_id=old_work_id,
                field="expected_total",
                origin=_FieldOrigin.MANUAL,
            )
            .first()
        )
        if et_manual is not None:
            actions.append(
                f"expected_total {et_manual.value} left on old work"
                f" #{old_work_id} — review"
            )

        # Decide: delete or keep based on any MANUAL rows
        manual_count = (
            session.query(_MetadataValue)
            .filter_by(
                entity_type="work",
                entity_id=old_work_id,
                origin=_FieldOrigin.MANUAL,
            )
            .count()
        )

        if manual_count > 0:
            actions.append(
                f"keep: old work #{old_work_id} '{old_work.title}'"
                f" has {manual_count} MANUAL row(s) — not deleted"
            )
        else:
            actions.append(
                f"delete: empty old work #{old_work_id} '{old_work.title}'"
            )
            if not dry_run:
                session.expire(old_work, ["episodes"])
                session.delete(old_work)
                session.flush()

    if not dry_run:
        session.commit()

    return actions
