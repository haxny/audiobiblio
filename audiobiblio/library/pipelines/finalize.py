"""finalize — Move a complete Work's files into a per-work subfolder.

Safety contract (never violated):
- Files are NEVER deleted — moves only (shutil.move).
- Name collision at destination → -2, -3, … suffix before extension.
- session.flush() before every file operation for session consistency.
- Existing directories are never renamed — we CREATE a new folder and move
  files INTO it.
- This is explicit-only: callers must pass dry_run=False to apply changes.

Target folder: {library_dir}/{program_folder}/{Author} - ({year}) {Album}/

program_folder is derived by the same logic as build_paths_for_episode (via
the extracted build_program_folder() helper) so the two share one code path.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from audiobiblio.core.db.models import Asset, AssetStatus, AssetType, Episode, Work

from .library import _slug, build_program_folder

log = structlog.get_logger()


@dataclass
class FinalizeReport:
    """Result of a finalize_work() call."""

    actions: list[str] = field(default_factory=list)
    applied: bool = False
    moved: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_work_dir(work: Work, first_ep: Episode, library_dir: Path) -> Path:
    """Compute the target per-work directory.

    Pattern: {library_dir}/{program_folder}/{Author} - ({year}) {Album}/

    Delegates program_folder derivation to build_program_folder() (extracted
    from build_paths_for_episode) so logic is not duplicated.
    """
    program_dir = library_dir / build_program_folder(first_ep, work)

    author = work.author or ""
    year = work.year
    if not year:
        pub = getattr(first_ep, "published_at", None)
        if pub:
            year = pub.year
    album = work.title or ""

    author_s = _slug(author) if author else ""
    album_s = _slug(album) if album else ""

    if author_s and year:
        folder = f"{author_s} - ({year}) {album_s}"
    elif author_s:
        folder = f"{author_s} - {album_s}"
    elif year:
        folder = f"({year}) {album_s}"
    else:
        folder = album_s or "Unknown"

    return program_dir / folder


def derive_curated_book_dir(work: Work, first_ep: Episode, dest_root: Path,
                            narrator: str | None, channel: str | None) -> Path | None:
    """Curated fiction layout (user convention, ALL unidecoded):

        {Autor} [audio]/{Autor} - ({rok}) {Titul} (cte {Interpret}, {kanal} {rok})

    Returns None when author or narrator is missing — a book must never land
    in the curated library with a half-empty name (Inbox instead).
    """
    author = _slug(work.author or "")
    if not author or not narrator:
        return None
    title = _slug(work.title or "")
    year = work.year
    rec_year = first_ep.published_at.year if getattr(first_ep, "published_at", None) else None
    src = " ".join(x for x in (channel, str(rec_year) if rec_year else None) if x)
    name = f"{author} - " + (f"({year}) " if year else "") + title
    name += f" (cte {_slug(narrator)}" + (f", {src})" if src else ")")
    return dest_root / f"{author} [audio]" / name


def derive_curated_collection_dir(dest_root: Path, program_label: str) -> Path:
    """Curated nonfiction layout: flat `{Program} ({kanal})/` folder —
    episode files land directly inside (the existing SFT convention)."""
    return dest_root / _slug(program_label)


def _resolve_dest(dest_dir: Path, filename: str) -> Path:
    """Return a collision-free destination path.

    If dest_dir/filename already exists, adds -2, -3, … before the extension
    until a free slot is found.  Never overwrites or deletes anything.
    """
    dest = dest_dir / filename
    if not dest.exists():
        return dest

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = dest_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _collect_complete_assets(session: Session, work: Work) -> list[Asset]:
    """Return all COMPLETE assets (with a file_path) for every episode in work."""
    assets: list[Asset] = []
    for ep in work.episodes:
        for asset in session.query(Asset).filter_by(episode_id=ep.id).all():
            if asset.status == AssetStatus.COMPLETE and asset.file_path:
                assets.append(asset)
    return assets


def _find_sidecars(src_path: Path) -> list[Path]:
    """Find non-tracked sibling files that share src_path's stem.

    A sidecar is any regular file in the same directory whose stem exactly
    matches src_path's stem (e.g. foo.nfo alongside foo.m4a).

    Must be called with the ORIGINAL source path (before any move) so sidecars
    are discovered in the correct location.
    """
    if not src_path.is_file():
        return []
    stem = src_path.stem
    sidecars: list[Path] = []
    try:
        for sibling in src_path.parent.iterdir():
            if sibling == src_path:
                continue
            if sibling.stem == stem and sibling.is_file():
                sidecars.append(sibling)
    except OSError:
        pass
    return sidecars


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_finalize(session: Session, work: Work, library_dir: Path) -> list[str]:
    """Return a human-readable plan of what finalize_work would do.

    Pure dry-run convenience wrapper — no files are touched.
    """
    return finalize_work(session, work, library_dir, dry_run=True).actions


def finalize_work(
    session: Session,
    work: Work,
    library_dir: Path,
    dry_run: bool = True,
    dest_dir_override: Path | None = None,
) -> FinalizeReport:
    """Move all COMPLETE asset files for a Work into a per-work subfolder.

    Args:
        session:     Active SQLAlchemy session.
        work:        The Work to finalize. Must have .episodes loaded.
        library_dir: Library root directory (caller-supplied; not read from config).
        dry_run:     If True, compute and return the plan without moving anything.

    Returns:
        FinalizeReport(actions, applied, moved, errors).

    Safety:
        - Files are NEVER deleted; shutil.move is the only file operation.
        - session.flush() is called before each shutil.move for session consistency
          (not a hard crash-safety guarantee — partial disk/DB divergence on
          mid-loop failure is a documented, recoverable-via-import-scan risk).
        - Collisions are resolved with -2, -3, … suffixes before the extension.
        - Already-tracked asset paths in the DB are updated to the new location.
    """
    report = FinalizeReport()

    if not work.episodes:
        report.errors.append("Work has no episodes — nothing to finalize")
        return report

    episodes = sorted(work.episodes, key=lambda e: (e.episode_number or 0, e.id))
    first_ep = episodes[0]

    # Curated-destination callers (finalize-to-curated spec) compute the
    # target with derive_curated_*_dir and pass it here; default behaviour
    # (per-work folder inside the library) is unchanged.
    dest_dir = dest_dir_override or _derive_work_dir(work, first_ep, library_dir)
    report.actions.append(f"Create folder: {dest_dir}")

    assets = _collect_complete_assets(session, work)
    if not assets:
        report.actions.append("No COMPLETE assets to move")
        return report

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    # Track processed paths to avoid double-moves (e.g. sidecar already queued)
    planned: set[str] = set()

    for asset in assets:
        src = Path(asset.file_path)
        if not src.is_file():
            report.errors.append(f"Missing on disk: {src}")
            continue

        if str(src) in planned:
            continue
        # Belt and braces: never re-move a file that is already inside the
        # destination folder (e.g. an asset whose DB path was updated by an
        # earlier sidecar sweep in this same run).
        if src.resolve().is_relative_to(dest_dir.resolve()):
            continue
        planned.add(str(src))

        # Non-audio assets (info.json, webpage backups) are archival metadata
        # no player needs next to the audio — they live in a _meta/ subfolder
        # so the book directory stays clean (user rule 2026-07-24).
        target_dir = dest_dir if asset.type == AssetType.AUDIO else dest_dir / "_meta"
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
        dest = _resolve_dest(target_dir, src.name)
        report.actions.append(f"Move: {src} -> {dest}")

        # Discover sidecars BEFORE moving (src still exists at this point)
        sidecars = _find_sidecars(src)

        if not dry_run:
            session.flush()
            shutil.move(str(src), str(dest))
            asset.file_path = str(dest.resolve())
            session.flush()
            report.moved += 1
            # Mark the destination as handled: another asset row may now hold
            # this exact path (updated by a sidecar sweep) — without this the
            # outer loop would re-move the file to a -2 suffix (preview/apply
            # divergence).
            planned.add(str(dest))
            planned.add(str(dest.resolve()))
            log.info("finalize_moved", src=str(src), dest=str(dest))

        # Sidecars: files sharing the same stem (e.g. .nfo, .info.json)
        for sidecar in sidecars:
            if str(sidecar) in planned:
                continue
            planned.add(str(sidecar))

            meta_dir = dest_dir / "_meta"
            if not dry_run:
                meta_dir.mkdir(parents=True, exist_ok=True)
            sidecar_dest = _resolve_dest(meta_dir, sidecar.name)
            report.actions.append(f"Move sidecar: {sidecar} -> {sidecar_dest}")

            if not dry_run:
                session.flush()
                shutil.move(str(sidecar), str(sidecar_dest))
                # If the sidecar is itself a tracked asset (e.g. META_JSON
                # sharing the audio stem), keep its DB path in sync so the
                # sweep never leaves dead paths behind.
                tracked = (
                    session.query(Asset)
                    .filter(Asset.file_path == str(sidecar.resolve()))
                    .all()
                )
                for tracked_asset in tracked:
                    tracked_asset.file_path = str(sidecar_dest.resolve())
                session.flush()
                report.moved += 1
                # Same guard as above: the tracked asset now points at
                # sidecar_dest — mark it planned so the outer loop skips it.
                planned.add(str(sidecar_dest))
                planned.add(str(sidecar_dest.resolve()))
                log.info(
                    "finalize_moved_sidecar",
                    src=str(sidecar),
                    dest=str(sidecar_dest),
                )

    report.applied = not dry_run
    return report
