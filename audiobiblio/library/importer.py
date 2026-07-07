"""
audiobiblio.library.importer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Import scanner — walk a directory tree, match audio files against the episode
database, and persist ImportFinding rows for human review.

Matching tiers (in order):

1. Dead-path recovery — file basename matches Asset.file_path OR
   extra["last_known_path"] of any MISSING asset → MATCHED reason "path"

2. Title match — _norm_title equality or SequenceMatcher > 0.9
   - If parsed album/author known: scoped to episodes in works/programs
     whose title/author fuzzy-matches the parsed album/author.
   - Else: global scan capped at GLOBAL_TITLE_CAP rows to avoid O(N) blowup.
     The cap is documented in details["global_cap"] when hit.
   - Single candidate → MATCHED reason "title"; multiple → UNKNOWN with
     candidates listed in details["candidates"].

3. DUPLICATE — matched episode already has a COMPLETE audio asset at a
   different existing path.

4. No match → UNKNOWN.

Layer: library (tier 3). Direct imports:
  - audiobiblio.core.*  (tier 5, downward ✓)
  - audiobiblio.dedupe.matching (tier 4, downward ✓)
  - audiobiblio.library.trash  (same tier, legal — no circularity)
  - audiobiblio.library.mediainfo  (same tier, legal)
  - audiobiblio.library.pipelines.library  (same tier, legal)

Trash import rationale: trash.py and importer.py are both in library/.
Neither imports the other transitively (trash has no library imports).
Direct import avoids the injection boilerplate used by dedupe/ for
cross-library boundaries.
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

import structlog

from audiobiblio.core.db.models import (
    Asset,
    AssetStatus,
    AssetType,
    FieldOrigin,
    ImportBucket,
    ImportFinding,
)
from audiobiblio.core.provenance import record_value
from audiobiblio.dedupe.matching import _norm_title, is_generic_title
from audiobiblio.library.mediainfo import apply_media_info
from audiobiblio.library.pipelines.library import build_paths_for_episode
from audiobiblio.tags.reader import find_audio_files, read_tags

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum episodes to scan globally (tier 2) when album/author not parseable.
# Prevents O(N) blowup on large libraries with unstructured filenames.
GLOBAL_TITLE_CAP = 5000

# Minimum SequenceMatcher ratio for a fuzzy title match.
TITLE_FUZZY_THRESHOLD = 0.9


# ---------------------------------------------------------------------------
# Stem parser
# ---------------------------------------------------------------------------

def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Regex patterns covering all 6 NAMING_CONVENTION shapes:
#
# Pattern 1: Author - Album
# Pattern 2: Author - (YYYY) Album
# Pattern 3: Author - (YYYY) Album - NN
# Pattern 4: Author - (YYYY) Album - NN Title
# Pattern 5: Author - (YYYY) Album (cte Performer, Publisher) - NN Title
# Pattern 6: Author - (YYYY) Album (cte Performer) - DNN Title  (multi-disc)
#
# The regex is applied to the bare stem (no extension, no leading path).
_STEM_RE = re.compile(
    r"^(?P<author>.+?)\s+-\s+"           # Author -
    r"(?:\((?P<year>\d{4})\)\s+)?"       # optional (YYYY)
    r"(?P<album>.+?)"                     # Album (lazy — stops before optional suffixes)
    r"(?:\s+\(cte\s+(?P<performer>[^,)]+)"  # optional (cte Performer
    r"(?:,\s*(?P<publisher>[^)]+))?\))?"    # optional , Publisher)
    r"(?:\s+-\s+(?P<track>\d{2,3})"      # optional - NN (2-3 digits)
    r"(?:\s+(?P<title>.+))?)?$"          # optional Title
)


def parse_stem(name: str) -> dict:
    """Parse a filename stem per NAMING_CONVENTION patterns 1–6.

    Returns a dict with any of the keys: author, year, album, track, title,
    performer, publisher.  Returns {} if the stem does not match any pattern.

    All values are raw strings (not normalised).
    """
    name = name.strip()
    if not name:
        return {}
    m = _STEM_RE.match(name)
    if not m:
        return {}
    result: dict = {}
    for key in ("author", "year", "album", "track", "title", "performer", "publisher"):
        val = m.group(key)
        if val is not None:
            result[key] = val.strip()
    return result


# ---------------------------------------------------------------------------
# ScanReport
# ---------------------------------------------------------------------------

@dataclass
class ScanReport:
    """Summary returned by scan_directory."""
    scan_id: str
    total: int = 0         # audio files visited
    skipped: int = 0       # already-known COMPLETE assets or resolved findings
    matched: int = 0       # MATCHED bucket
    duplicate: int = 0     # DUPLICATE bucket
    unknown: int = 0       # UNKNOWN bucket
    new_findings: int = 0  # rows inserted
    updated_findings: int = 0  # rows updated (re-scan)


# ---------------------------------------------------------------------------
# Internal matching helpers
# ---------------------------------------------------------------------------

def _fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _get_missing_basenames(session) -> dict[str, list[int]]:
    """Return {basename: [asset_ids]} for all MISSING assets.

    Covers both file_path and extra["last_known_path"].
    """
    result: dict[str, list[int]] = {}
    assets = (
        session.query(Asset)
        .filter(Asset.status == AssetStatus.MISSING)
        .all()
    )
    for asset in assets:
        # Check file_path basename
        if asset.file_path:
            bn = Path(asset.file_path).name
            result.setdefault(bn, []).append(asset.id)
        # Check extra["last_known_path"] basename
        lkp = (asset.extra or {}).get("last_known_path")
        if lkp:
            bn = Path(lkp).name
            result.setdefault(bn, []).append(asset.id)
    return result


def _get_complete_basenames(session) -> set[str]:
    """Return basenames of all COMPLETE asset file_paths."""
    assets = (
        session.query(Asset.file_path)
        .filter(
            Asset.status == AssetStatus.COMPLETE,
            Asset.file_path.isnot(None),
        )
        .all()
    )
    return {Path(row.file_path).name for row in assets}


def _complete_asset_file_paths(session) -> set[str]:
    """Return the set of all COMPLETE asset file_paths (normalised)."""
    rows = (
        session.query(Asset.file_path)
        .filter(
            Asset.status == AssetStatus.COMPLETE,
            Asset.file_path.isnot(None),
        )
        .all()
    )
    return {row.file_path for row in rows}


def _match_by_path(
    session, basename: str, missing_map: dict[str, list[int]]
) -> Optional[int]:
    """Return episode_id if basename matches a MISSING asset, else None."""
    if basename not in missing_map:
        return None
    asset_ids = missing_map[basename]
    # Take first matching asset (basenames are typically unique)
    asset = session.query(Asset).filter(Asset.id == asset_ids[0]).first()
    if asset:
        return asset.episode_id
    return None


def _match_by_title(
    session, norm_parsed_title: str, parsed: dict
) -> tuple[str, list[int]]:
    """Search episodes by normalised title.

    Returns (result_type, episode_ids) where result_type is:
      - "single" → one candidate found
      - "multiple" → more than one
      - "none" → no match

    Scoping rules:
    - If parsed album or author is available, restrict to episodes whose
      work title or series/program name fuzzy-matches.
    - Otherwise: global scan capped at GLOBAL_TITLE_CAP.
    """
    from audiobiblio.core.db.models import Episode, Work, Series, Program  # local import avoids top-level circularity risk
    from sqlalchemy import select

    album = parsed.get("album", "")
    author = parsed.get("author", "")

    # Collect candidate episode ids
    if album or author:
        # Scoped search: find works/programs whose name matches parsed metadata
        episodes_scoped = _scope_episodes(session, album, author)
        candidates = _filter_by_title(norm_parsed_title, episodes_scoped)
    else:
        # Global search with cap
        all_episodes = session.query(Episode).limit(GLOBAL_TITLE_CAP).all()
        candidates = _filter_by_title(norm_parsed_title, all_episodes)

    if len(candidates) == 1:
        return "single", [candidates[0].id]
    if len(candidates) > 1:
        return "multiple", [ep.id for ep in candidates]
    return "none", []


def _scope_episodes(session, album: str, author: str):
    """Return episodes from works whose title fuzzy-matches album and/or author."""
    from audiobiblio.core.db.models import Episode, Work

    norm_album = _norm_title(album)
    norm_author = _norm_title(author)

    all_works = session.query(Work).all()
    matching_work_ids: list[int] = []
    for work in all_works:
        work_norm = _norm_title(work.title)
        author_norm = _norm_title(work.author)
        album_match = (
            (norm_album and work_norm == norm_album)
            or (norm_album and _fuzzy_ratio(norm_album, work_norm) >= TITLE_FUZZY_THRESHOLD)
        )
        author_match = (
            (norm_author and author_norm == norm_author)
            or (norm_author and _fuzzy_ratio(norm_author, author_norm) >= TITLE_FUZZY_THRESHOLD)
        )
        if album_match or author_match:
            matching_work_ids.append(work.id)

    if not matching_work_ids:
        return []

    return (
        session.query(Episode)
        .filter(Episode.work_id.in_(matching_work_ids))
        .all()
    )


def _filter_by_title(norm_parsed_title: str, episodes) -> list:
    """Filter episodes whose normalised title matches norm_parsed_title."""
    if not norm_parsed_title:
        return []
    matched = []
    for ep in episodes:
        ep_norm = _norm_title(ep.title)
        if ep_norm == norm_parsed_title or _fuzzy_ratio(ep_norm, norm_parsed_title) >= TITLE_FUZZY_THRESHOLD:
            matched.append(ep)
    return matched


def _has_complete_audio_at_different_path(session, episode_id: int, new_path: str) -> bool:
    """Return True if episode has a COMPLETE AUDIO asset at a path other than new_path."""
    asset = (
        session.query(Asset)
        .filter_by(episode_id=episode_id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
        .first()
    )
    if not asset:
        return False
    if asset.file_path is None:
        return False
    return asset.file_path != new_path


# ---------------------------------------------------------------------------
# scan_directory
# ---------------------------------------------------------------------------

def scan_directory(
    session,
    root: Path,
    scan_id: str,
    inbox: bool = False,
    limit: Optional[int] = None,
) -> ScanReport:
    """Walk *root* and match every audio file against the episode database.

    Skips:
    - Paths already tracked by a COMPLETE asset (file already in library).
    - Paths in import_findings with status != "new" (resolved — don't re-open).

    Re-scan behaviour on "new" findings:
    - The existing row is updated (scan_id, bucket, episode_id, details).
    - created_at is preserved.

    Args:
        session: SQLAlchemy session.
        root: Directory to walk (recursive).
        scan_id: Unique identifier for this scan run (caller provides, e.g. UUID).
        inbox: Reserved for future inbox-specific logic (currently unused).
        limit: Max audio files to process (None = all).

    Returns:
        ScanReport with counts.
    """
    root = Path(root)
    report = ScanReport(scan_id=scan_id)

    # Build lookup structures upfront (one pass over DB)
    complete_paths = _complete_asset_file_paths(session)
    missing_map = _get_missing_basenames(session)

    # Build lookup: path → existing finding (for idempotence)
    existing_findings: dict[str, ImportFinding] = {
        f.path: f
        for f in session.query(ImportFinding).all()
    }

    # find_audio_files returns strings or Path objects depending on version;
    # normalise to Path for consistent .stem / .name access.
    audio_files = [Path(p) for p in find_audio_files(root)]
    processed = 0

    for audio_path in audio_files:
        path_str = str(audio_path)

        # --- Skip already-in-library complete assets ---
        if path_str in complete_paths:
            report.skipped += 1
            continue

        # --- Skip resolved findings ---
        existing = existing_findings.get(path_str)
        if existing and existing.status in ("accepted", "ignored"):
            report.skipped += 1
            continue

        # --- Apply limit ---
        if limit is not None and processed >= limit:
            break
        processed += 1
        report.total += 1

        # --- Read tags (graceful degradation on failure) ---
        tags: dict = {}
        tags_unreadable = False
        try:
            tags = read_tags(path_str) or {}
        except Exception as exc:
            log.debug("importer.tags_unreadable", path=path_str, err=str(exc))
            tags_unreadable = True

        # --- Parse stem ---
        stem = audio_path.stem
        parsed = parse_stem(stem)

        # --- Build details skeleton ---
        details: dict = {
            "tags": {k: v for k, v in tags.items() if isinstance(v, str)},
            "parsed_stem": parsed,
        }
        if tags_unreadable:
            details["tags_unreadable"] = True

        # --- Check generic title ---
        parsed_title = parsed.get("title", "") or tags.get("title", "")
        if parsed_title and is_generic_title(parsed_title):
            details["generic_title"] = True

        # --- Tier 1: Dead-path recovery ---
        basename = audio_path.name
        episode_id: Optional[int] = _match_by_path(session, basename, missing_map)
        match_reason: Optional[str] = None
        bucket = ImportBucket.UNKNOWN
        candidates: list[int] = []

        if episode_id is not None:
            match_reason = "path"
            bucket = ImportBucket.MATCHED
        else:
            # --- Tier 2: Title match ---
            norm_parsed = _norm_title(parsed.get("title") or tags.get("title") or "")
            if norm_parsed:
                result_type, ep_ids = _match_by_title(session, norm_parsed, parsed)
                if result_type == "single":
                    episode_id = ep_ids[0]
                    match_reason = "title"
                    bucket = ImportBucket.MATCHED
                elif result_type == "multiple":
                    candidates = ep_ids
                    details["candidates"] = candidates
                    bucket = ImportBucket.UNKNOWN
            # No title → UNKNOWN

        # --- Tier 3: Duplicate check ---
        if bucket == ImportBucket.MATCHED and episode_id is not None:
            if _has_complete_audio_at_different_path(session, episode_id, path_str):
                bucket = ImportBucket.DUPLICATE

        # --- Populate details ---
        if match_reason:
            details["match_reason"] = match_reason

        # --- Persist finding (insert or update) ---
        if existing and existing.status == "new":
            # Update existing "new" row
            existing.scan_id = scan_id
            existing.bucket = bucket
            existing.episode_id = episode_id
            existing.details = details
            session.add(existing)
            report.updated_findings += 1
        elif existing is None:
            # Insert new row
            finding = ImportFinding(
                scan_id=scan_id,
                path=path_str,
                bucket=bucket,
                episode_id=episode_id,
                details=details,
                status="new",
            )
            session.add(finding)
            report.new_findings += 1

        # --- Tally bucket ---
        if bucket == ImportBucket.MATCHED:
            report.matched += 1
        elif bucket == ImportBucket.DUPLICATE:
            report.duplicate += 1
        else:
            report.unknown += 1

    session.commit()
    log.info(
        "importer.scan_complete",
        scan_id=scan_id,
        total=report.total,
        matched=report.matched,
        duplicate=report.duplicate,
        unknown=report.unknown,
    )
    return report


# ---------------------------------------------------------------------------
# accept_finding
# ---------------------------------------------------------------------------

def accept_finding(
    session,
    finding: ImportFinding,
    move: bool = False,
    library_dir: Optional[Path] = None,
    trash_fn: Optional[Callable] = None,
) -> list[str]:
    """Accept a finding: link the file to its episode as an AUDIO asset.

    For MATCHED findings:
    - If the episode already has a MISSING AUDIO asset → repair it:
        set status=COMPLETE, file_path=finding.path, remove last_known_path.
    - Otherwise → create a new AUDIO asset (status=COMPLETE).
    - Record FILE provenance for the path.
    - Call apply_media_info.
    - If move=True: compute target via build_paths_for_episode, shutil.move the
      file (collision → add -2, -3 suffix), then update asset.file_path.

    For DUPLICATE findings:
    - Replace flow: trash the existing file via trash_fn, then link the new one.
    - If trash_fn is None → raise ValueError (caller must supply trash function).

    Args:
        session: SQLAlchemy session.
        finding: The ImportFinding row to accept.
        move: If True, move file to library-managed path.
        library_dir: Root library dir (required when move=True).
        trash_fn: Callable(path: Path, library_dir: Path) → Path; required for
            DUPLICATE accept to trash the old file.

    Returns:
        List of log/diagnostic strings (empty on success with no special actions).

    Raises:
        ValueError: If finding is DUPLICATE and trash_fn is None.
    """
    if finding.bucket == ImportBucket.DUPLICATE and trash_fn is None:
        raise ValueError(
            "trash_fn is required to accept a DUPLICATE finding "
            "(the existing file must be trashed before linking the new one)"
        )

    episode_id = finding.episode_id
    new_path = Path(finding.path)
    log_msgs: list[str] = []

    # --- Handle DUPLICATE: trash old file first ---
    if finding.bucket == ImportBucket.DUPLICATE and episode_id is not None:
        old_asset = (
            session.query(Asset)
            .filter_by(episode_id=episode_id, type=AssetType.AUDIO, status=AssetStatus.COMPLETE)
            .first()
        )
        if old_asset and old_asset.file_path:
            old_path = Path(old_asset.file_path)
            if old_path.exists():
                trash_fn(old_path, library_dir)
                log_msgs.append(f"trashed: {old_path}")
            old_asset.status = AssetStatus.MISSING
            session.add(old_asset)
            session.flush()

    # --- Find or create the AUDIO asset ---
    existing_asset = (
        session.query(Asset)
        .filter_by(episode_id=episode_id, type=AssetType.AUDIO)
        .first()
    )

    if existing_asset and existing_asset.status == AssetStatus.MISSING:
        # Repair the MISSING asset
        existing_asset.status = AssetStatus.COMPLETE
        existing_asset.file_path = str(new_path)
        # Clear last_known_path from extra
        if existing_asset.extra:
            extra = dict(existing_asset.extra)
            extra.pop("last_known_path", None)
            existing_asset.extra = extra if extra else None
        asset = existing_asset
        session.add(asset)
    elif existing_asset is None or finding.bucket == ImportBucket.DUPLICATE:
        # Create a fresh asset (or replace after trash)
        asset = Asset(
            episode_id=episode_id,
            type=AssetType.AUDIO,
            status=AssetStatus.COMPLETE,
            file_path=str(new_path),
        )
        session.add(asset)
    else:
        # Existing COMPLETE asset at the same path (idempotent re-accept)
        asset = existing_asset
        asset.file_path = str(new_path)
        session.add(asset)

    session.flush()

    # --- Record FILE provenance ---
    if episode_id is not None:
        record_value(
            session,
            entity_type="episode",
            entity_id=episode_id,
            field="file_path",
            value=str(new_path),
            origin=FieldOrigin.FILE,
            source=str(new_path),
        )

    # --- Move file if requested ---
    if move:
        from audiobiblio.core.db.models import Episode as _Episode
        episode = session.get(_Episode, episode_id)
        final_path = _move_to_library(new_path, episode, library_dir)
        asset.file_path = str(final_path)
        session.add(asset)
        session.flush()
        log_msgs.append(f"moved: {new_path} → {final_path}")

    # --- Apply media info ---
    apply_media_info(session, asset, Path(asset.file_path))

    # --- Mark finding resolved ---
    finding.status = "accepted"
    finding.resolved_at = datetime.utcnow()
    session.add(finding)
    session.commit()

    return log_msgs


def _move_to_library(
    src: Path, episode, library_dir: Optional[Path]
) -> Path:
    """Move src to the library-managed path for episode.

    Computes target via build_paths_for_episode.  Handles name collisions
    by appending -2, -3, … before the extension.
    """
    paths = build_paths_for_episode(episode)
    base_dir: Path = paths["base_dir"]
    stem: str = paths["stem"]
    ext = src.suffix  # preserve original extension

    base_dir.mkdir(parents=True, exist_ok=True)
    target = base_dir / f"{stem}{ext}"

    # Handle collision
    if target.exists() and target != src:
        counter = 2
        while True:
            candidate = base_dir / f"{stem}-{counter}{ext}"
            if not candidate.exists():
                target = candidate
                break
            counter += 1

    shutil.move(str(src), str(target))
    return target


# ---------------------------------------------------------------------------
# ignore_finding
# ---------------------------------------------------------------------------

def ignore_finding(session, finding: ImportFinding) -> None:
    """Mark a finding as ignored (no asset will be created)."""
    finding.status = "ignored"
    finding.resolved_at = datetime.utcnow()
    session.add(finding)
    session.commit()
